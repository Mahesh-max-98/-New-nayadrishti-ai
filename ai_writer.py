
import os
import requests

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-3.1-flash-lite:generateContent"
)


def write_summary(title, description, source_name, article_url):
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    prompt = f"""
You are a careful bilingual news editor for NayaDrishti.

Write a short, accurate news post in clear Nepali or English.
Use only the supplied facts. Never invent details.
Include a clear headline and source attribution.
Keep the post concise and suitable for social media.

Source: {source_name}
Title: {title}
Description: {description}
Article link: {article_url}

Return only the finished news post.
"""

    response = requests.post(
        GEMINI_URL,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json"
        },
        json={
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 500
            }
        },
        timeout=45
    )

    if not response.ok:
        raise RuntimeError(
            f"Gemini API error {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no generated response.")

    parts = candidates[0].get("content", {}).get("parts", [])
    result = "\n".join(
        part.get("text", "") for part in parts
    ).strip()

    if not result:
        raise RuntimeError("Gemini returned an empty summary.")

    return result
    
