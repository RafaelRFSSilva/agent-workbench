from ollama import chat

MODEL_NAME = "gpt-oss:20b"


def main() -> None:
    """Run the first interaction with the local language model."""

    response = chat(
        model=MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": ("Reply exactly with: Python connected to the local model"),
            }
        ],
        stream=False,
    )

    print(response.message.content)


if __name__ == "__main__":
    main()
