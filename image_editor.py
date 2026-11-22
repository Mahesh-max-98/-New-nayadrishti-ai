"""Image editing and variation helpers backed by FLUX.2 Klein 9B.

The model supports reference-image conditioning. This module does not claim
pixel-perfect masked inpainting; selective mask editing remains a later capability.
"""
from image_provider import generate_image, ImageProviderError


def edit_image(
    source_image_bytes,
    instruction,
    width=1024,
    height=768,
    additional_references=None,
    conversation_context="",
):
    instruction = str(instruction or "").strip()
    if not instruction:
        raise ImageProviderError("Describe what you want to change in the image.")
    if len(instruction) > 5000:
        raise ImageProviderError("The editing instruction is too long.")

    prompt = (
        "Edit the provided reference photograph/image according to the user's instruction. "
        "Treat reference image 0 as the source image. Preserve its main subject, identity, "
        "camera viewpoint, composition, perspective and all unmentioned objects. "
        "Change only what the user explicitly asks to change. Do not replace the scene or "
        "create a new unrelated image. If the user asks to preserve an object or feature, "
        "keep it visually consistent with the source. Maintain natural lighting, realistic "
        "textures and coherent edges. Earlier conversation context is provided only to "
        "understand continuity; do not reapply old edits unless the latest instruction asks. "
        "Earlier context: " + str(conversation_context or "")[:2500] + ". "
        "Latest user editing instruction: " + instruction
    )
    references = [source_image_bytes]
    references.extend(list(additional_references or [])[:3])
    return generate_image(
        prompt=prompt,
        reference_images=references,
        width=width,
        height=height,
    )


def create_variation(source_image_bytes, instruction="", width=1024, height=768):
    direction = str(instruction or "").strip()
    prompt = (
        "Create a tasteful variation of reference image 0. Preserve the same main subject, "
        "identity, recognizable scene and overall visual intent. Explore a slightly different "
        "composition and visual details without changing the subject into something unrelated. "
        + (("User's variation direction: " + direction) if direction else
           "Make a subtle alternate version with natural detail and balanced lighting.")
    )
    return generate_image(
        prompt=prompt,
        reference_images=[source_image_bytes],
        width=width,
        height=height,
    )
