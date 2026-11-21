import os
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def write_summary(title, description, source_name, article_url):
    """Create a very short Nepali-first bilingual Facebook post with Groq."""
    api_key = os.getenv("GROQ_API_KEY")
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    prompt = f"""
You are the careful news editor for NayaDrishti, a Nepal-focused news page.

Write a VERY SHORT Facebook news post using only facts explicitly present in
the supplied title and description. Never invent or assume facts. If something
is unclear, omit it. Do not claim you opened or independently verified the link.

STRICT OUTPUT RULES:
- Write Nepali first, then English.
- Keep the ENTIRE post to 3-4 short lines total.
- Line 1: a short Nepali headline with one relevant emoji.
- Line 2: one short Nepali sentence with the key update.
- Line 3: a short English headline with one relevant emoji.
- Line 4: one short English sentence faithfully translating the Nepali update.
- Keep each sentence concise. Do not add background or extra details.
- Use natural, correct Nepali and clear, simple English.
- Include 2 relevant emojis in every post: one in each headline.
- Do NOT show the source name, source label, article URL, "Read more", credits,
  hashtags, greetings, explanations, or extra commentary.
- Do not use markdown dividers or bullet points.
- Return only the finished post, with no blank lines.

SUPPLIED INFORMATION (for fact-checking and writing only; do not display source):
Source name: {source_name}
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
                    "content": (
                        "You are a precise bilingual news editor. Follow the "
                        "requested format exactly. Return only the finished "
                        "short post, not analysis or explanations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            # GPT-OSS spends part of its completion budget on reasoning.
            # Hide that field and keep reasoning effort low so there is room
            # for the actual Facebook post in message.content.
            "include_reasoning": False,
            "reasoning_effort": "low",
            "max_completion_tokens": 600,
        },
        timeout=45,
    )

    if not response.ok:
        detail = response.text[:500].replace("\\n", " ")
        raise RuntimeError(
            f"Groq API error {response.status_code}: {detail}"
        )

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("Groq returned no generated response.")

    choice = choices[0]
    message = choice.get("message") or {}
    result = message.get("content") or ""

    # Some compatible APIs may return text as content blocks rather than a
    # single string. Extract only text blocks; never use private reasoning.
    if isinstance(result, list):
        result = "\\n".join(
            block.get("text", "")
            for block in result
            if isinstance(block, dict) and block.get("type") == "text"
        )

    result = str(result).strip()
    if not result:
        finish_reason = choice.get("finish_reason", "unknown")
        usage = data.get("usage") or {}
        completion_tokens = usage.get("completion_tokens", "unknown")
        raise RuntimeError(
            "Groq returned no post text "
            f"(finish_reason={finish_reason}, "
            f"completion_tokens={completion_tokens}). "
            "Check the configured GROQ_MODEL and Groq response."
        )

    return result
