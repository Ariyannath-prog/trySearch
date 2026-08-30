"""Open-model settings and model-output JSON parsing."""

import json
import os
import re

def parse_json_from_model(text_value):
    text_value = (text_value or '').strip()
    text_value = re.sub(r'^```(?:json)?\s*', '', text_value, flags=re.I)
    text_value = re.sub(r'\s*```$', '', text_value)
    try:
        return json.loads(text_value)
    except json.JSONDecodeError:
        match = re.search(r'(\{.*\}|\[.*\])', text_value, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(1))

def open_model_settings():
    if os.environ.get('HF_TOKEN'):
        return {
            'configured': True,
            'provider': 'Hugging Face Inference Providers',
            'base_url': os.environ.get('HF_BASE_URL', 'https://router.huggingface.co/v1'),
            'model': os.environ.get('HF_MODEL', 'openai/gpt-oss-120b:preferred'),
            'headers': {'Authorization': f"Bearer {os.environ['HF_TOKEN']}"},
        }
    if os.environ.get('OLLAMA_BASE_URL'):
        return {
            'configured': True,
            'provider': 'Ollama',
            'base_url': os.environ['OLLAMA_BASE_URL'],
            'model': os.environ.get('OLLAMA_MODEL', 'gpt-oss:20b'),
            'headers': {},
        }
    return {
        'configured': False,
        'provider': 'Hugging Face Inference Providers or Ollama',
        'base_url': os.environ.get('HF_BASE_URL', 'https://router.huggingface.co/v1'),
        'model': os.environ.get('HF_MODEL', 'openai/gpt-oss-120b:preferred'),
        'headers': {},
    }
