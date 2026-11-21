
import os
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def write_summary(title, description, source_name, article_url):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    prompt = f"""
Write a short, accurate NayaDrishti news post.
Use only the supplied facts. Do not invent information.
Use clear Nepali or English.
Include a headline and source attribution.

Source: {source_name}
Title: {title}
Description: {description}
Link: {article_url}

Return only the news post.
"""

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful bilingual news editor."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        },
        timeout=45
    )

    response.raise_for_status()
    data = response.json()

    return data["choices"][0]["message"]["content"].strip()
    
