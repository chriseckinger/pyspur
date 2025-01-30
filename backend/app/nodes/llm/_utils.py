# type: ignore
import base64
import json
import logging
import os
import re
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, cast
from pathlib import Path
from docx2python import docx2python

import litellm
from dotenv import load_dotenv
from litellm import acompletion
from ollama import AsyncClient
from pydantic import BaseModel, Field
from tenacity import AsyncRetrying, stop_after_attempt, wait_random_exponential

from ...utils.file_utils import encode_file_to_base64_data_url
from ...utils.path_utils import resolve_file_path, is_external_url

from ._providers import OllamaOptions, setup_azure_configuration

# uncomment for debugging litellm issues
# litellm.set_verbose=True
load_dotenv()

# Enable parameter dropping for unsupported parameters
litellm.drop_params = True

# Clean up Azure API base URL if needed
azure_api_base = os.getenv("AZURE_OPENAI_API_BASE", "").rstrip("/")
if azure_api_base.endswith("/openai"):
    azure_api_base = azure_api_base.rstrip("/openai")
os.environ["AZURE_OPENAI_API_BASE"] = azure_api_base

# Set OpenAI base URL if provided
openai_base_url = os.getenv("OPENAI_API_BASE")
if openai_base_url:
    litellm.api_base = openai_base_url

# If Azure OpenAi is configured, set it as the default provider
if os.getenv("AZURE_OPENAI_API_KEY"):
    litellm.api_key = os.getenv("AZURE_OPENAI_API_KEY")


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    AZURE_OPENAI = "azure"
    DEEPSEEK = "deepseek"


class ModelConstraints(BaseModel):
    max_tokens: int
    min_temperature: float = 0.0
    max_temperature: float = 1.0
    supports_JSON_output: bool = True


class LLMModel(BaseModel):
    id: str
    provider: LLMProvider
    name: str
    constraints: ModelConstraints


class LLMModels(str, Enum):
    # OpenAI Models
    GPT_4O_MINI = "openai/gpt-4o-mini"
    GPT_4O = "openai/gpt-4o"
    O1_PREVIEW = "openai/o1-preview"
    O1_MINI = "openai/o1-mini"
    O1 = "openai/o1"
    O1_2024_12_17 = "openai/o1-2024-12-17"
    O1_MINI_2024_09_12 = "openai/o1-mini-2024-09-12"
    O1_PREVIEW_2024_09_12 = "openai/o1-preview-2024-09-12"
    GPT_4_TURBO = "openai/gpt-4-turbo"
    CHATGPT_4O_LATEST = "openai/chatgpt-4o-latest"

    # Azure OpenAI Models
    AZURE_GPT_4 = "azure/gpt-4"
    AZURE_GPT_4_TURBO = "azure/gpt-4-turbo"
    AZURE_GPT_35_TURBO = "azure/gpt-35-turbo"

    # Anthropic Models
    CLAUDE_3_5_SONNET_LATEST = "anthropic/claude-3-5-sonnet-latest"
    CLAUDE_3_5_HAIKU_LATEST = "anthropic/claude-3-5-haiku-latest"
    CLAUDE_3_OPUS_LATEST = "anthropic/claude-3-opus-latest"

    # Google Models
    GEMINI_2_0_FLASH_EXP = "gemini/gemini-2.0-flash-exp"
    GEMINI_1_5_PRO = "gemini/gemini-1.5-pro"
    GEMINI_1_5_FLASH = "gemini/gemini-1.5-flash"
    GEMINI_1_5_PRO_LATEST = "gemini/gemini-1.5-pro-latest"
    GEMINI_1_5_FLASH_LATEST = "gemini/gemini-1.5-flash-latest"

    # Deepseek Models
    DEEPSEEK_CHAT = "deepseek/deepseek-chat"
    DEEPSEEK_REASONER = "deepseek/deepseek-reasoner"

    # Ollama Models
    OLLAMA_PHI4 = "ollama/phi4"
    OLLAMA_LLAMA3_3_70B = "ollama/llama3.3:70b"
    OLLAMA_LLAMA3_3_8B = "ollama/llama3.3:8b"
    OLLAMA_LLAMA3_2_8B = "ollama/llama3.2:8b"
    OLLAMA_LLAMA3_2_1B = "ollama/llama3.2:1b"
    OLLAMA_LLAMA3_8B = "ollama/llama3"
    OLLAMA_GEMMA_2 = "ollama/gemma2"
    OLLAMA_GEMMA_2_2B = "ollama/gemma2:2b"
    OLLAMA_MISTRAL = "ollama/mistral"
    OLLAMA_CODELLAMA = "ollama/codellama"
    OLLAMA_MIXTRAL = "ollama/mixtral-8x7b-instruct-v0.1"
    OLLAMA_DEEPSEEK_R1 = "ollama/deepseek-r1"

    @classmethod
    def get_model_info(cls, model_id: str) -> LLMModel:
        model_registry = {
            # OpenAI Models - all have temperature up to 2.0
            cls.GPT_4O_MINI.value: LLMModel(
                id=cls.GPT_4O_MINI.value,
                provider=LLMProvider.OPENAI,
                name="GPT-4O Mini",
                constraints=ModelConstraints(max_tokens=16384, max_temperature=2.0),
            ),
            cls.GPT_4O.value: LLMModel(
                id=cls.GPT_4O.value,
                provider=LLMProvider.OPENAI,
                name="GPT-4O",
                constraints=ModelConstraints(max_tokens=16384, max_temperature=2.0),
            ),
            cls.O1_PREVIEW.value: LLMModel(
                id=cls.O1_PREVIEW.value,
                provider=LLMProvider.OPENAI,
                name="O1 Preview",
                constraints=ModelConstraints(max_tokens=32768, max_temperature=2.0),
            ),
            cls.O1_MINI.value: LLMModel(
                id=cls.O1_MINI.value,
                provider=LLMProvider.OPENAI,
                name="O1 Mini",
                constraints=ModelConstraints(max_tokens=65536, max_temperature=2.0),
            ),
            cls.O1.value: LLMModel(
                id=cls.O1.value,
                provider=LLMProvider.OPENAI,
                name="O1",
                constraints=ModelConstraints(max_tokens=100000, max_temperature=2.0),
            ),
            cls.O1_2024_12_17.value: LLMModel(
                id=cls.O1_2024_12_17.value,
                provider=LLMProvider.OPENAI,
                name="O1 (2024-12-17)",
                constraints=ModelConstraints(max_tokens=100000, max_temperature=2.0),
            ),
            cls.O1_MINI_2024_09_12.value: LLMModel(
                id=cls.O1_MINI_2024_09_12.value,
                provider=LLMProvider.OPENAI,
                name="O1 Mini (2024-09-12)",
                constraints=ModelConstraints(max_tokens=65536, max_temperature=2.0),
            ),
            cls.O1_PREVIEW_2024_09_12.value: LLMModel(
                id=cls.O1_PREVIEW_2024_09_12.value,
                provider=LLMProvider.OPENAI,
                name="O1 Preview (2024-09-12)",
                constraints=ModelConstraints(max_tokens=32768, max_temperature=2.0),
            ),
            cls.GPT_4_TURBO.value: LLMModel(
                id=cls.GPT_4_TURBO.value,
                provider=LLMProvider.OPENAI,
                name="GPT-4 Turbo",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.CHATGPT_4O_LATEST.value: LLMModel(
                id=cls.CHATGPT_4O_LATEST.value,
                provider=LLMProvider.OPENAI,
                name="ChatGPT-4 Optimized Latest",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            # Azure OpenAI Models
            cls.AZURE_GPT_4.value: LLMModel(
                id=cls.AZURE_GPT_4.value,
                provider=LLMProvider.AZURE_OPENAI,
                name="Azure GPT-4",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.AZURE_GPT_4_TURBO.value: LLMModel(
                id=cls.AZURE_GPT_4_TURBO.value,
                provider=LLMProvider.AZURE_OPENAI,
                name="Azure GPT-4 Turbo",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.AZURE_GPT_35_TURBO.value: LLMModel(
                id=cls.AZURE_GPT_35_TURBO.value,
                provider=LLMProvider.AZURE_OPENAI,
                name="Azure GPT-3.5 Turbo",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            # Anthropic Models
            cls.CLAUDE_3_5_SONNET_LATEST.value: LLMModel(
                id=cls.CLAUDE_3_5_SONNET_LATEST.value,
                provider=LLMProvider.ANTHROPIC,
                name="Claude 3.5 Sonnet Latest",
                constraints=ModelConstraints(
                    max_tokens=8192, max_temperature=1.0, supports_JSON_output=False
                ),
            ),
            cls.CLAUDE_3_5_HAIKU_LATEST.value: LLMModel(
                id=cls.CLAUDE_3_5_HAIKU_LATEST.value,
                provider=LLMProvider.ANTHROPIC,
                name="Claude 3.5 Haiku Latest",
                constraints=ModelConstraints(
                    max_tokens=8192, max_temperature=1.0, supports_JSON_output=False
                ),
            ),
            cls.CLAUDE_3_OPUS_LATEST.value: LLMModel(
                id=cls.CLAUDE_3_OPUS_LATEST.value,
                provider=LLMProvider.ANTHROPIC,
                name="Claude 3 Opus Latest",
                constraints=ModelConstraints(
                    max_tokens=4096, max_temperature=1.0, supports_JSON_output=False
                ),
            ),
            # Google Models
            cls.GEMINI_1_5_PRO.value: LLMModel(
                id=cls.GEMINI_1_5_PRO.value,
                provider=LLMProvider.GEMINI,
                name="Gemini 1.5 Pro",
                constraints=ModelConstraints(max_tokens=8192, max_temperature=1.0),
            ),
            cls.GEMINI_1_5_FLASH.value: LLMModel(
                id=cls.GEMINI_1_5_FLASH.value,
                provider=LLMProvider.GEMINI,
                name="Gemini 1.5 Flash",
                constraints=ModelConstraints(max_tokens=8192, max_temperature=1.0),
            ),
            cls.GEMINI_1_5_PRO_LATEST.value: LLMModel(
                id=cls.GEMINI_1_5_PRO_LATEST.value,
                provider=LLMProvider.GEMINI,
                name="Gemini 1.5 Pro Latest",
                constraints=ModelConstraints(max_tokens=8192, max_temperature=1.0),
            ),
            cls.GEMINI_1_5_FLASH_LATEST.value: LLMModel(
                id=cls.GEMINI_1_5_FLASH_LATEST.value,
                provider=LLMProvider.GEMINI,
                name="Gemini 1.5 Flash Latest",
                constraints=ModelConstraints(max_tokens=8192, max_temperature=1.0),
            ),
            # Deepseek Models
            cls.DEEPSEEK_CHAT.value: LLMModel(
                id=cls.DEEPSEEK_CHAT.value,
                provider=LLMProvider.DEEPSEEK,
                name="Deepseek Chat",
                constraints=ModelConstraints(
                    max_tokens=8192, max_temperature=2.0, supports_JSON_output=False
                ),
            ),
            cls.DEEPSEEK_REASONER.value: LLMModel(
                id=cls.DEEPSEEK_REASONER.value,
                provider=LLMProvider.DEEPSEEK,
                name="Deepseek Reasoner",
                constraints=ModelConstraints(
                    max_tokens=8192, max_temperature=2.0, supports_JSON_output=False
                ),
            ),
            # Ollama Models
            cls.OLLAMA_PHI4.value: LLMModel(
                id=cls.OLLAMA_PHI4.value,
                provider=LLMProvider.OLLAMA,
                name="Phi 4",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_LLAMA3_3_70B.value: LLMModel(
                id=cls.OLLAMA_LLAMA3_3_70B.value,
                provider=LLMProvider.OLLAMA,
                name="Llama 3.3 (70B)",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_LLAMA3_3_8B.value: LLMModel(
                id=cls.OLLAMA_LLAMA3_3_8B.value,
                provider=LLMProvider.OLLAMA,
                name="Llama 3.3 (8B)",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_LLAMA3_2_8B.value: LLMModel(
                id=cls.OLLAMA_LLAMA3_2_8B.value,
                provider=LLMProvider.OLLAMA,
                name="Llama 3.2 (8B)",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_LLAMA3_2_1B.value: LLMModel(
                id=cls.OLLAMA_LLAMA3_2_1B.value,
                provider=LLMProvider.OLLAMA,
                name="Llama 3.2 (1B)",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_LLAMA3_8B.value: LLMModel(
                id=cls.OLLAMA_LLAMA3_8B.value,
                provider=LLMProvider.OLLAMA,
                name="Llama 3 (8B)",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_GEMMA_2.value: LLMModel(
                id=cls.OLLAMA_GEMMA_2.value,
                provider=LLMProvider.OLLAMA,
                name="Gemma 2",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_GEMMA_2_2B.value: LLMModel(
                id=cls.OLLAMA_GEMMA_2_2B.value,
                provider=LLMProvider.OLLAMA,
                name="Gemma 2 (2B)",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_MISTRAL.value: LLMModel(
                id=cls.OLLAMA_MISTRAL.value,
                provider=LLMProvider.OLLAMA,
                name="Mistral",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_CODELLAMA.value: LLMModel(
                id=cls.OLLAMA_CODELLAMA.value,
                provider=LLMProvider.OLLAMA,
                name="CodeLlama",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_MIXTRAL.value: LLMModel(
                id=cls.OLLAMA_MIXTRAL.value,
                provider=LLMProvider.OLLAMA,
                name="Mixtral 8x7B Instruct",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
            cls.OLLAMA_DEEPSEEK_R1.value: LLMModel(
                id=cls.OLLAMA_DEEPSEEK_R1.value,
                provider=LLMProvider.OLLAMA,
                name="Deepseek R1",
                constraints=ModelConstraints(max_tokens=4096, max_temperature=2.0),
            ),
        }
        return model_registry.get(model_id)


class ModelInfo(BaseModel):
    model: LLMModels = Field(
        LLMModels.GPT_4O, description="The LLM model to use for completion"
    )
    max_tokens: Optional[int] = Field(
        ...,
        ge=1,
        le=65536,
        description="Maximum number of tokens the model can generate",
    )
    temperature: Optional[float] = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Temperature for randomness, between 0.0 and 1.0",
    )
    top_p: Optional[float] = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Top-p sampling value, between 0.0 and 1.0",
    )


def create_messages(
    system_message: str,
    user_message: str,
    few_shot_examples: Optional[List[Dict[str, str]]] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    messages = [{"role": "system", "content": system_message}]
    if few_shot_examples:
        for example in few_shot_examples:
            messages.append({"role": "user", "content": example["input"]})
            messages.append({"role": "assistant", "content": example["output"]})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages


def create_messages_with_images(
    system_message: str,
    base64_image: str,
    user_message: str = "",
    few_shot_examples: Optional[List[Dict]] = None,
    history: Optional[List[Dict]] = None,
) -> List[Dict[str, str]]:
    messages = [
        {"role": "system", "content": [{"type": "text", "text": system_message}]}
    ]
    if few_shot_examples:
        for example in few_shot_examples:
            messages.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": example["input"]}],
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": example["img"]}}
                    ],
                }
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": example["output"]}],
                }
            )
    if history:
        messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": base64_image}}],
        }
    )
    if user_message:
        messages[-1]["content"].append({"type": "text", "text": user_message})
    return messages


def async_retry(*dargs, **dkwargs):
    def decorator(f: Callable) -> Callable:
        r = AsyncRetrying(*dargs, **dkwargs)

        async def wrapped_f(*args, **kwargs):
            async for attempt in r:
                with attempt:
                    return await f(*args, **kwargs)

        return wrapped_f

    return decorator


@async_retry(
    wait=wait_random_exponential(min=30, max=120),
    stop=stop_after_attempt(3),
    retry=lambda e: not isinstance(
        e,
        (
            litellm.exceptions.AuthenticationError,
            ValueError,
            litellm.exceptions.RateLimitError,
        ),
    ),
)
async def completion_with_backoff(**kwargs) -> str:
    """
    Calls the LLM completion endpoint with backoff.
    Supports Azure OpenAI, standard OpenAI, or Ollama based on the model name.
    """
    try:
        model = kwargs.get("model", "")
        logging.info("=== LLM Request Configuration ===")
        logging.info(f"Requested Model: {model}")

        # Use Azure if either 'azure/' is prefixed or if an Azure API key is provided and not using Ollama
        if model.startswith("azure/") or (
            os.getenv("AZURE_OPENAI_API_KEY") and not model.startswith("ollama/")
        ):
            azure_kwargs = setup_azure_configuration(kwargs)
            logging.info(f"Using Azure config for model: {azure_kwargs['model']}")
            try:
                response = await acompletion(**azure_kwargs)
                return response.choices[0].message.content
            except Exception as e:
                logging.error(f"Error calling Azure OpenAI: {e}")
                raise

        elif model.startswith("ollama/"):
            logging.info("=== Ollama Configuration ===")
            response = await acompletion(**kwargs)
            return response.choices[0].message.content
        else:
            logging.info("=== Standard Configuration ===")
            response = await acompletion(**kwargs)
            return response.choices[0].message.content

    except Exception as e:
        logging.error(f"=== LLM Request Error ===")
        # Create a save copy of kwargs without sensitive information
        save_config = kwargs.copy()
        save_config["api_key"] = "********" if "api_key" in save_config else None
        logging.error(f"Error occurred with configuration: {save_config}")
        logging.error(f"Error type: {type(e).__name__}")
        logging.error(f"Error message: {str(e)}")
        if hasattr(e, "response"):
            logging.error(
                f"Response status: {getattr(e.response, 'status_code', 'N/A')}"
            )
            logging.error(f"Response body: {getattr(e.response, 'text', 'N/A')}")
        raise e


async def generate_text(
    messages: List[Dict[str, str]],
    model_name: str,
    temperature: float = 0.5,
    json_mode: bool = False,
    max_tokens: int = 100000,
    api_base: Optional[str] = None,
    url_variables: Optional[Dict[str, str]] = None,
    output_json_schema: Optional[str] = None,
    output_schema: Optional[Dict[str, Any]] = None,
) -> str:
    kwargs = {
        "model": model_name,
        "max_tokens": max_tokens,
        "messages": messages,
        "temperature": temperature,
    }
    if model_name == "deepseek/deepseek-reasoner":
        kwargs.pop("temperature")

    response = ""

    # Get model info to check if it supports JSON output
    model_info = LLMModels.get_model_info(model_name)
    supports_json = model_info and model_info.constraints.supports_JSON_output

    # Only process JSON schema if the model supports it
    if supports_json:
        if output_json_schema is None and output_schema is None:
            output_schema = {"output": "string"}
            output_json_schema = {
                "type": "object",
                "properties": {"output": {"type": "string"}},
                "required": ["output"],
            }
        elif output_json_schema is None and output_schema is not None:
            output_json_schema = convert_output_schema_to_json_schema(output_schema)
        elif output_json_schema is not None and output_json_schema.strip() != "":
            output_json_schema = json.loads(output_json_schema)
        output_json_schema["additionalProperties"] = False

        # check if the model supports response format
        if "response_format" in litellm.get_supported_openai_params(model=model_name):
            if litellm.supports_response_schema(
                model=model_name, custom_llm_provider=None
            ):
                if (
                    "name" not in output_json_schema
                    and "schema" not in output_json_schema
                ):
                    output_json_schema = {
                        "schema": output_json_schema,
                        "strict": True,
                        "name": "output",
                    }
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": output_json_schema,
                }
            else:
                kwargs["response_format"] = {"type": "json_object"}
                if output_schema:
                    schema_for_prompt = json.dumps(output_schema)
                elif output_json_schema:
                    schema_for_prompt = json.dumps(output_json_schema)
                else:
                    schema_for_prompt = json.dumps({"output": "string"})
                system_message = next(
                    message for message in messages if message["role"] == "system"
                )
                system_message["content"] += (
                    "\nYou must respond with valid JSON only. No other text before or after the JSON Object. The JSON Object must adhere to this schema: "
                    + schema_for_prompt
                )

    if json_mode and supports_json:
        if model_name.startswith("ollama"):
            options = OllamaOptions(temperature=temperature, max_tokens=max_tokens)
            raw_response = await ollama_with_backoff(
                model=model_name,
                options=options,
                messages=messages,
                format="json",
                api_base=api_base,
            )
            response = raw_response
        # Handle Gemini models with URL variables
        elif model_name.startswith("gemini") and url_variables:
            # Transform messages to include URL content
            transformed_messages = []
            for msg in messages:
                if msg["role"] == "user":
                    content = [{"type": "text", "text": msg["content"]}]
                    # Add any URL variables as image_url or other supported types
                    for _, url in url_variables.items():
                        if url:  # Only add if URL is provided
                            # Check if the URL is a base64 data URL
                            if is_external_url(url) or url.startswith("data:"):
                                content.append(
                                    {"type": "image_url", "image_url": {"url": url}}
                                )
                            else:
                                # For file paths, encode the file with appropriate MIME type
                                try:
                                    # Use the new path resolution utility
                                    file_path = resolve_file_path(url)
                                    logging.info(f"Reading file from: {file_path}")

                                    # Check if file is a DOCX file
                                    if str(file_path).lower().endswith('.docx'):
                                        # Convert DOCX to XML
                                        xml_content = convert_docx_to_xml(str(file_path))
                                        # Encode the XML content directly
                                        data_url = f"data:text/xml;base64,{base64.b64encode(xml_content.encode()).decode()}"
                                    else:
                                        data_url = encode_file_to_base64_data_url(str(file_path))

                                    content.append(
                                        {
                                            "type": "image_url",
                                            "image_url": {"url": data_url},
                                        }
                                    )
                                except Exception as e:
                                    logging.error(f"Error reading file {url}: {str(e)}")
                                    raise
                    msg["content"] = content
                transformed_messages.append(msg)
            kwargs["messages"] = transformed_messages
            raw_response = await completion_with_backoff(**kwargs)
            response = raw_response
        else:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": "You must respond with valid JSON only. No other text before or after the JSON Object.",
                },
            )
            raw_response = await completion_with_backoff(**kwargs)
            response = raw_response
    else:
        raw_response = await completion_with_backoff(**kwargs)
        response = raw_response

    # For models that don't support JSON output, wrap the response in a JSON structure
    if not supports_json:
        sanitized_response = response.replace('"', '\\"').replace("\n", "\\n")
        # Check for provider-specific fields
        if hasattr(raw_response, 'choices') and len(raw_response.choices) > 0:
            if hasattr(raw_response.choices[0].message, 'provider_specific_fields'):
                provider_fields = raw_response.choices[0].message.provider_specific_fields
                return json.dumps({
                    "output": sanitized_response,
                    "provider_specific_fields": provider_fields
                })
        return f'{{"output": "{sanitized_response}"}}'

    # Ensure response is valid JSON for models that support it
    if supports_json:
        try:
            json.loads(response)
            return response
        except json.JSONDecodeError:
            logging.error(f"Response is not valid JSON: {response}")
            # Try to fix common json issues
            if not response.startswith("{"):
                # Extract JSON if there is extra text
                json_match = re.search(r"\{.*\}", response, re.DOTALL)
                if json_match:
                    response = json_match.group(0)
                    try:
                        json.loads(response)
                        return response
                    except json.JSONDecodeError:
                        pass

            # If all attempts to parse JSON fail, wrap the response in a JSON structure
            sanitized_response = response.replace('"', '\\"').replace("\n", "\\n")
            # Check for provider-specific fields
            if hasattr(raw_response, 'choices') and len(raw_response.choices) > 0:
                if hasattr(raw_response.choices[0].message, 'provider_specific_fields'):
                    provider_fields = raw_response.choices[0].message.provider_specific_fields
                    return json.dumps({
                        "output": sanitized_response,
                        "provider_specific_fields": provider_fields
                    })
            return f'{{"output": "{sanitized_response}"}}'

    return response


def convert_output_schema_to_json_schema(
    output_schema: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Convert a simple output schema to a JSON schema.
    Simple output schema is a dictionary with field names and types.
    Types can be one of 'str', 'int', 'float' or 'bool'.
    """
    json_schema = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    for field, field_type in output_schema.items():
        if field_type == "str" or field_type == "string":
            json_schema["properties"][field] = {"type": "string"}
        elif field_type == "int" or field_type == "integer":
            json_schema["properties"][field] = {"type": "integer"}
        elif field_type == "float" or field_type == "number":
            json_schema["properties"][field] = {"type": "number"}
        elif field_type == "bool" or field_type == "boolean":
            json_schema["properties"][field] = {"type": "boolean"}
        json_schema["required"].append(field)
    return json_schema


def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


@async_retry(wait=wait_random_exponential(min=30, max=120), stop=stop_after_attempt(3))
async def ollama_with_backoff(
    model: str,
    messages: list[dict[str, str]],
    format: Optional[str | dict[str, Any]] = None,
    options: Optional[OllamaOptions] = None,
    api_base: Optional[str] = None,
) -> str:
    """
    Make an async Ollama API call with exponential backoff retry logic.

    Args:
        model: The name of the Ollama model to use
        messages: List of message dictionaries with 'role' and 'content'
        options: OllamaOptions instance with model parameters
        max_retries: Maximum number of retries
        initial_wait: Initial wait time between retries in seconds
        max_wait: Maximum wait time between retries in seconds

    Returns:
        Either a string response or a validated Pydantic model instance
    """
    client = AsyncClient(host=api_base)
    response = await client.chat(
        model=model.replace("ollama/", ""),
        messages=messages,
        format=format,
        options=(options or OllamaOptions()).to_dict(),
    )
    return response.message.content


def convert_docx_to_xml(file_path: str) -> str:
    """
    Convert a DOCX file to XML format.
    Args:
        file_path: Path to the DOCX file
    Returns:
        XML string representation of the DOCX file
    """
    try:
        with docx2python(file_path) as docx_content:
            # Convert the document content to XML format
            xml_content = f"<?xml version='1.0' encoding='UTF-8'?>\n<document>\n"

            # Add metadata
            xml_content += "<metadata>\n"
            for key, value in docx_content.properties.items():
                if value:  # Only add non-empty properties
                    xml_content += f"<{key}>{value}</{key}>\n"
            xml_content += "</metadata>\n"

            # Add document content
            xml_content += "<content>\n"
            for paragraph in docx_content.text:
                if paragraph:  # Skip empty paragraphs
                    xml_content += f"<paragraph>{paragraph}</paragraph>\n"
            xml_content += "</content>\n"
            xml_content += "</document>"

            return xml_content
    except Exception as e:
        logging.error(f"Error converting DOCX to XML: {str(e)}")
        raise
