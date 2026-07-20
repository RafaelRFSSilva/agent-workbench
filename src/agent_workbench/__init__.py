from ollama import chat

MODEL_NAME = "gpt-oss:20b"

def main() -> None:
    """Execute first interaction with Python and the model local"""

    response = chat(model=MODEL_NAME,
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "Aswer exactly with: "
                                "Python on with model local"
                            ),
                        }
                    ],
                    stream=False,)
    
    print(response.message.content)

if __name__ == "__main__":
    main()