import os
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def write_summary(title, description, source_name, article_url):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    prompt = f"""
Create a concise, original NayaDrishti news post using only the facts below.
Do not invent facts, quotes, dates, names, numbers, or causes. If information is
uncertain, say so or omit it. Do not copy source wording. Use either clear Nepali
or English, choosing the language that best fits the source. Keep it to 1–8 short
lines, include a short headline and source attribution, and do not add hashtags
unless relevant.

Source: {source_name}
Title: {title}
Description: {description}
Link: {article_url}

Return only the post text.
"""
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": "You are a careful bilingual news editor. Accuracy is more important than speed."},
                {"role": "user", "content": prompt}
            ]
        },
        timeout=45
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()
