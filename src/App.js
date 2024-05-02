import React, { useState, useEffect } from "react";
import { Container, Form, Button } from "react-bootstrap";
import axios from "axios";
import ChatInterface from "./ChatInterface";

function App() {
  const [file, setFile] = useState(null);
  const [api, setApi] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gpt-3.5-turbo");
  const [languages, setLanguages] = useState([]);
  const [selectedLanguage, setSelectedLanguage] = useState("eng");
  const [ocrText, setOcrText] = useState("");

  useEffect(() => {
    const fetchLanguages = async () => {
      try {
        const response = await axios.get(
          `${process.env.REACT_APP_API_URL}languages`
        );
        setLanguages(response.data.languages);
        setSelectedLanguage(response.data.languages[0]);
      } catch (error) {
        console.error("Failed to fetch languages", error);
      }
    };
    fetchLanguages();
  }, []);

  const handleFileChange = (event) => {
    setFile(event.target.files[0]);
  };

  const handleApiChange = (event) => {
    setApi(event.target.value);
  };

  const handleApiKeyChange = (event) => {
    setApiKey(event.target.value);
  };

  const handleModelChange = (event) => {
    setModel(event.target.value);
  };

  const handleLanguageChange = (event) => {
    setSelectedLanguage(event.target.value);
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    const formData = new FormData();
    formData.append("file", file);
    formData.append("language", selectedLanguage);

    try {
      const response = await axios.post(
        `${process.env.REACT_APP_API_URL}ocr`,
        formData,
        {
          headers: {
            "Content-Type": "multipart/form-data",
          },
        }
      );
      if (response.status === 200) {
        setOcrText(response.data.OCRText);
      } else {
        console.error("Failed to process the document:", response.data);
      }
    } catch (error) {
      console.error("Error sending the file:", error);
    }
  };

  return (
    <Container className="p-3">
      <Form onSubmit={handleSubmit}>
        <Form.Group controlId="formFile" className="mb-3">
          <Form.Label>Upload PDF</Form.Label>
          <Form.Control type="file" onChange={handleFileChange} />
        </Form.Group>
        <Form.Group controlId="formApiSelect" className="mb-3">
          <Form.Label>Select AI API</Form.Label>
          <Form.Select value={api} onChange={handleApiChange}>
            <option value="openai">OpenAI</option>
            <option value="claude">Claude</option>
          </Form.Select>
        </Form.Group>
        <Form.Group controlId="formModelSelect" className="mb-3">
          <Form.Label>Select Model</Form.Label>
          <Form.Select value={model} onChange={handleModelChange}>
            <option value="gpt-3.5-turbo">GPT-3.5 Turbo</option>
            <option value="gpt-4">GPT-4</option>
            <option value="claude-instant-1">Claude Instant 1</option>
            <option value="claude-2">Claude 2</option>
          </Form.Select>
        </Form.Group>
        <Form.Group controlId="formLanguageSelect" className="mb-3">
          <Form.Label>Select OCR Language</Form.Label>
          <Form.Select value={selectedLanguage} onChange={handleLanguageChange}>
            {languages.map((lang) => (
              <option key={lang} value={lang}>
                {lang}
              </option>
            ))}
          </Form.Select>
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>API Key</Form.Label>
          <Form.Control
            type="text"
            placeholder="Enter API Key"
            value={apiKey}
            onChange={handleApiKeyChange}
          />
        </Form.Group>
        <Button variant="primary" type="submit">
          Submit
        </Button>
      </Form>
      {ocrText && (
        <ChatInterface
          ocrText={ocrText}
          api={api}
          apiKey={apiKey}
          model={model}
        />
      )}
    </Container>
  );
}

export default App;
