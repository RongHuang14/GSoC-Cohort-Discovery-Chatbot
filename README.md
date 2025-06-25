# LangChain GraphQL Converter

A simple application built with LangChain and FastAPI that converts natural language queries into GraphQL queries.

## Installation
python version >= 3.9.0

1. After cloning the project, install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up your OpenAI API key in the `.env` file:
```
OPENAI_API_KEY=your_api_key_here
```

## Running the Application

Start the server:
```bash
python app.py
```

The server will run at http://localhost:8000

## API Usage

Send a POST request to the `/convert` endpoint:

```bash
curl -X POST "http://localhost:8000/convert" \
     -H "Content-Type: application/json" \
     -d '{"text": "Get all users names and emails"}'
```

## Example Response

```json
{
    "query": "query { users { name email } }",
    "explanation": "This query will return the names and email addresses of all users"
}
```

## Frontend(Chainlit)
Start with auth (Required by Chainlit https://docs.chainlit.io/data-persistence/history):
```bash
./start_with_auth.sh
```
