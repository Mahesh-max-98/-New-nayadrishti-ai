import os
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def write_summary(title, description, source_name, article_url):
    """Create a concise Nepali-first, bilingual Facebook news post with Groq."""
    api_key = os.getenv("GROQ_API_KEY")
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    prompt = f"""
You are the careful news editor for NayaDrishti, a Nepal-focused news page.

Create a SHORT Facebook-style news post using only the supplied information.
Do not add facts, dates, names, numbers, quotes, causes, or claims that are not
explicitly supported by the supplied title and description. If information is
unclear, leave it out. Do not pretend to have opened or verified the article.

Format:
नेपाली शीर्षक
नेपालीमा 2-4 short sentences.

English headline
A faithful English version in 1-3 short sentences.

Source: {source_name}
Read more: {article_url}

Keep the whole post concise, neutral, readable, and suitable for Facebook.
Do not add hashtags unless they are clearly useful. Return only the post.

SUPPLIED INFORMATION
Source: {source_name}
Title: {title}
Description: {description}
Article URL: {article_url}
"""

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a precise bilingual news editor. Accuracy is more important than style.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 500,
        },
        timeout=45,
    )

    if not response.ok:
        detail = response.text[:500].replace("\n", " ")
        raise RuntimeError(
            f"Groq API error {response.status_code}: {detail}"
        )

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("Groq returned no generated response.")

    result = (
        choices[0].get("message", {}).get("content") or ""
    ).strip()

    if not result:
        raise RuntimeError("Groq returned an empty summary.")

    return result
