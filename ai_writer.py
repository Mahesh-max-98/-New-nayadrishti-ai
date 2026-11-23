import os
import re

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def write_summary(title, description, source_name, article_url):
    """Write a clear, factual Nepali Facebook news post with no timestamp or image text."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    article_text = re.sub(r"\s+", " ", description or "").strip()[:7000]
    if len(article_text) < 80:
        raise ValueError("The source article does not contain enough readable information for a reliable news post.")

    prompt = f"""
तपाईं NayaDrishti का अनुभवी, जिम्मेवार नेपाली समाचार सम्पादक हुनुहुन्छ।
मूल समाचारको सामग्री मात्र आधार मानेर फेसबुकका लागि स्पष्ट, स्वाभाविक र पढ्न सजिलो नेपाली पोस्ट लेख्नुहोस्।

लेखन ढाँचा:
१. पहिलो लाइन: आकर्षक तर निष्पक्ष, छोटो नेपाली शीर्षक।
२. त्यसपछि २ देखि ५ लाइन: के भयो, कहाँ भयो, कसलाई/कुन निकायलाई असर पर्छ र उपलब्ध मुख्य तथ्य सरल भाषामा।
३. कुनै पनि अवस्थामा स्रोत, प्रकाशक, वेबसाइट वा मिडियाको नाम पोस्टमा नलेख्नुहोस्। स्रोत विवरण dashboard मा मात्र सुरक्षित हुन्छ।

कडा सम्पादकीय नियम:
- जम्मा 3 देखि 6 लाइन लेख्नुहोस्। खाली लाइन नराख्नुहोस्.
- स्वाभाविक नेपाली प्रयोग गर्नुहोस्; अनावश्यक अंग्रेजी शब्द हटाउनुहोस्।
- मुख्य तथ्य पहिले राख्नुहोस्। दोहोरिएका कुरा, लामो भूमिका र क्लिकबेट हटाउनुहोस्।
- स्रोतमा नभएको नाम, स्थान, संख्या, मिति, उद्धरण, कारण वा परिणाम नबनाउनुहोस्।
- अनुमान, आरोप, प्रस्ताव वा अपुष्ट दाबीलाई पुष्टि भएको तथ्यका रूपमा नलेख्नुहोस्।
- स्रोतमा तथ्य अस्पष्ट भए निश्चित भाषा प्रयोग नगर्नुहोस्।
- प्रकाशन समय, घडीको समय, "अहिले", "भर्खरै" वा मिति/समयको छुट्टै लाइन नथप्नुहोस्।
- फोटो, चित्र, थम्बनेल वा डिजाइनबारे केही नलेख्नुहोस्।
- ह्यासट्याग, इमोजी, बाहिरी लिंक, अंग्रेजी शीर्षक वा अतिरिक्त टिप्पणी नलेख्नुहोस्; अन्तिममा इमोजी कोडले थप्नेछ।
- "स्रोत", "श्रोत", "Source", "Credit", "Courtesy", "Via", "साभार" वा मूल प्रकाशक/मिडियाको नाम पोस्टमा कतै पनि नराख्नुहोस्।
- पोस्टको पाठ मात्र फर्काउनुहोस्।

मूल स्रोत: {source_name}
मूल शीर्षक: {title}
मूल समाचार सामग्री: {article_text}
लेख URL (तथ्यको प्राथमिक स्रोत यही सामग्री हो): {article_url}
"""
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "तपाईं निष्पक्ष नेपाली समाचार सम्पादक हुनुहुन्छ। प्रमाणित सामग्रीमा आधारित 4-7 लाइनको नेपाली फेसबुक पोस्ट मात्र लेख्नुहोस्।",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.15,
            "max_completion_tokens": 550,
        },
        timeout=45,
    )
    if not response.ok:
        detail = response.text[:450].replace("\n", " ").replace("\r", " ")
        raise RuntimeError(f"Groq API error {response.status_code}: {detail}")

    choices = response.json().get("choices") or []
    if not choices:
        raise RuntimeError("Groq returned no generated response.")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "\n".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    content = str(content).replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    lines = []
    publisher_name = (source_name or "").strip()
    for raw_line in content.splitlines():
        line = re.sub(r"^[-•*\\s]+", "", raw_line).strip()
        if not line:
            continue
        if re.search(r"\\b(\\d{1,2}:\\d{2}\\s?(?:AM|PM)?|प्रकाशित समय|प्रकाशन समय)\\b", line, re.I):
            continue
        # Remove source-credit lines and inline credit tails regardless of punctuation/style.
        if re.match(r"^(?:स्रोत|श्रोत|source|credit|courtesy|via|साभार)\\b", line, re.I):
            continue
        line = re.sub(
            r"\\s*(?:[-|—–]\\s*)?(?:स्रोत|श्रोत|source|credit|courtesy|via|साभार)\\s*[:：–—-].*$",
            "", line, flags=re.I,
        ).strip()
        # Never include the original publisher name in the public post.
        if publisher_name and re.search(re.escape(publisher_name), line, re.I):
            continue
        if not line:
            continue
        lines.append(line)

    if len(lines) < 3:
        raise RuntimeError("Groq returned too little factual detail for a news post.")
    # Avoid a post that is only a headline plus repeated filler.
    unique_lines = []
    for line in lines:
        if not unique_lines or line.casefold() != unique_lines[-1].casefold():
            unique_lines.append(line)
    lines = [line for line in unique_lines if not re.match(r"^(स्रोत|source)\s*:", line, re.I)]
    lines = lines[:6]
    if not (3 <= len(lines) <= 6):
        raise RuntimeError("Generated news post did not meet the required line format.")
    lines[-1] = lines[-1].rstrip(" ।.!?") + " 📰"
    return "\n".join(lines)
