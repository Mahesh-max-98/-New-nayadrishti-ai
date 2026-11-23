import os
import re
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def write_summary(title, description, source_name, article_url):
    """Create a concise, factual Nepali-only Facebook news post (3-8 lines)."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    prompt = f"""
तपाईं NayaDrishti का जिम्मेवार नेपाली समाचार सम्पादक हुनुहुन्छ।
तल दिइएको मूल समाचार सामग्रीका आधारमा Facebook का लागि नेपाली भाषामा छोटो पोस्ट लेख्नुहोस्।

अनिवार्य नियम:
- जम्मा ३ देखि ८ लाइन मात्र लेख्नुहोस्।
- पहिलो लाइनमा छोटो, स्पष्ट र तथ्यमा आधारित शीर्षक राख्नुहोस्।
- त्यसपछि १ देखि ५ छोटा लाइनमा मुख्य घटना, स्थान, मिति, संख्या वा निर्णय (स्रोतमा भए मात्र) बुझाउनुहोस्।
- अन्तिम लाइनमा "स्रोत: {source_name}" लेख्नुहोस्।
- स्वाभाविक र सरल नेपाली प्रयोग गर्नुहोस्।
- अंग्रेजी शीर्षक नदोहोर्‍याउनुहोस्, ह्यासट्याग नराख्नुहोस्, लिंक नलेख्नुहोस्।
- सामग्रीमा नभएको तथ्य, संख्या, उद्धरण वा निष्कर्ष नथप्नुहोस्।
- प्रस्तावलाई निर्णय, अनुमानलाई पुष्टि भएको तथ्य, वा पुरानो घटनालाई ताजा समाचार नबनाउनुहोस्।
- पर्याप्त तथ्य नभए उपलब्ध तथ्य मात्र लेख्नुहोस् र अड्कल नगर्नुहोस्।
- कुनै फोटो वा फोटोसम्बन्धी निर्देशन नलेख्नुहोस्।
- केवल पोस्टको पाठ फर्काउनुहोस्, कुनै व्याख्या होइन।

मूल स्रोत: {source_name}
मूल शीर्षक: {title}
समाचार सामग्री: {description}
मूल लेख URL (सन्दर्भका लागि मात्र): {article_url}
"""
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "तपाईं तथ्यमा आधारित नेपाली समाचार लेखक हुनुहुन्छ। ३-८ लाइनको नेपाली पोस्ट मात्र फर्काउनुहोस्।"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_completion_tokens": 500,
        },
        timeout=45,
    )
    if not response.ok:
        detail = response.text[:500].replace("\n", " ").replace("\r", " ")
        raise RuntimeError(f"Groq API error {response.status_code}: {detail}")
    choices = response.json().get("choices") or []
    if not choices:
        raise RuntimeError("Groq returned no generated response.")
    message = choices[0].get("message") or {}
    result = message.get("content") or ""
    if isinstance(result, list):
        result = "\n".join(
            block.get("text", "") for block in result
            if isinstance(block, dict) and block.get("type") == "text"
        )
    result = str(result).replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    lines = [re.sub(r"^[-•*\\s]+", "", line).strip() for line in result.splitlines()]
    lines = [line for line in lines if line]
    # Normalize the final credit so every post identifies the original source.
    credit = f"स्रोत: {source_name}"
    lines = [line for line in lines if not line.lower().startswith(("स्रोत:", "source:"))]
    if len(lines) < 2:
        raise RuntimeError("Groq returned too little detail for a 3-line news post.")
    lines = lines[:7]
    lines.append(credit)
    return "\n".join(lines[:8])
