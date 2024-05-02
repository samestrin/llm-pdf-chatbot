import React, { useState } from "react";
import { Container, Form, Button } from "react-bootstrap";
import axios from "axios";

const MAX_TOKENS = 9160;

function ChatInterface({ ocrText, api, apiKey, model }) {
  const [questionAsked, setQuestionAsked] = useState("");
  const [response, setResponse] = useState("");

  const handleQuestionChange = (event) => {
    setQuestionAsked(event.target.value);
  };

  const handleSubmitQuestion = async (event) => {
    event.preventDefault();
    console.log(ocrText);

    const prompt = `You will now be answering questions specifically based on pdfText; you must limit your scope of knowledge to this. Please review the provided pdfText carefully; do not show any work. Once you understand pdfText, attempt to answer questionAsked based on codeToReview. If you can, reply with "Answer" and the answer, if you can't, reply with "Answer Not Available"; do not reply with anything else.
  
  pdfText:
  ${ocrText}
  
  questionAsked:
  ${questionAsked}`;

    let url, headers, data;

    if (api === "openai") {
      url = "https://api.openai.com/v1/completions";
      headers = {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      };
      data = {
        prompt: prompt,
        model: model, // Ensure this is dynamically set based on selection
        max_tokens: MAX_TOKENS,
      };
    } else if (api === "claude") {
      url = "https://api.anthropic.com";
      headers = {
        "Content-Type": "application/json",
        accept: "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      };
      data = {
        model: model,
        max_tokens: MAX_TOKENS,
        messages: [
          {
            role: "user",
            content: prompt,
          },
        ],
      };
    }

    try {
      const response = await axios.post(url, data, { headers });
      console.log(response.data);
      if (api === "openai") {
        setResponse(response.data.choices[0].text);
      } else if (api === "claude") {
        // Assume the response structure for Claude is the same or adjust accordingly
        setResponse(
          response.data.content.map((content) => content.text).join(" ")
        );
      }
    } catch (error) {
      console.error("Error querying the AI:", error);
      setResponse("Error processing your question.");
    }
  };

  return (
    <Container className="p-3">
      <div className="response-box">
        <h5>AI Response:</h5>
        <p>{response}</p>
      </div>
      <Form onSubmit={handleSubmitQuestion}>
        <Form.Group className="mb-3">
          <Form.Label>Message PDF...</Form.Label>
          <Form.Control
            type="text"
            placeholder="Ask your question..."
            value={questionAsked}
            onChange={handleQuestionChange}
          />
        </Form.Group>
        <Button variant="primary" type="submit">
          Ask
        </Button>
      </Form>
    </Container>
  );
}

export default ChatInterface;
