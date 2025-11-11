from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
import pdfplumber
import requests
import os
import uuid
import json

load_dotenv()
app = FastAPI()
MAX_RESUME_CHARS = 12000

# ---------------------- Pydantic Models ----------------------
class EducationItem(BaseModel):
    degree: str
    institution: str
    year: Optional[str] = None

class ExperienceItem(BaseModel):
    role: str
    company: str
    location: Optional[str] = None
    duration: Optional[str] = None
    bullets: List[str]

class ProjectItem(BaseModel):
    name: str
    description: str

class ResumeStructured(BaseModel):
    summary: str
    core_skills: List[str]
    experience: List[ExperienceItem]
    projects: List[ProjectItem]
    education: List[EducationItem]
    interests: Optional[List[str]] = []

class DocumentOutput(BaseModel):
    content: str

# ---------------------- PDF Text Extractor ----------------------
def extract_pdf_text(file_path: str) -> str:
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text[:MAX_RESUME_CHARS]

# ---------------------- Groq API Call ----------------------
def generate_with_groq(prompt: str) -> str:
    api_url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Return ONLY valid JSON. No extra text."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 6000
    }
    r = requests.post(api_url, headers=headers, json=payload)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"]
    raise HTTPException(status_code=500, detail=f"Groq API Error: {r.text}")

# ---------------------- Clean LLM Output ----------------------
def clean_llm_output(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned[3:-3].strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned

# ---------------------- Safe Construction ----------------------
def safe_resume_structured(raw: str) -> ResumeStructured:
    try:
        data = json.loads(clean_llm_output(raw))
        return ResumeStructured(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse resume JSON: {str(e)}\nRaw: {raw}")

def safe_document_output(raw: str) -> DocumentOutput:
    try:
        data = json.loads(clean_llm_output(raw))
        return DocumentOutput(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse document JSON: {str(e)}\nRaw: {raw}")

# ---------------------- Core Handler ----------------------
async def handle_generation(cleaned_json: dict, resume_file: UploadFile, doc_type: str):
    temp = f"tmp_{uuid.uuid4().hex}.pdf"
    try:
        with open(temp, "wb") as f:
            f.write(await resume_file.read())
        resume_text = extract_pdf_text(temp)
    finally:
        if os.path.exists(temp):
            os.remove(temp)

    # ---------------------- Prompt Building ----------------------
    if doc_type == "Enhanced Resume":
        prompt = f"""
You are an expert scholarship application writer.

DOCUMENT TYPE: {doc_type}

APPLICANT RESUME:
-----------------
{resume_text}

SCHOLARSHIP DETAILS:
-------------------
{json.dumps(cleaned_json, indent=2)}

TASK: Generate ONLY JSON in this schema:
{{
  "summary": "",
  "core_skills": [],
  "experience": [ {{ "role": "", "company": "", "location": "", "duration": "", "bullets": [] }} ],
  "projects": [ {{ "name": "", "description": "" }} ],
  "education": [ {{ "degree": "", "institution": "", "year": "" }} ],
  "interests": []
}}

RULES:
- JSON ONLY
- Do not fabricate degrees or projects
- Tailor resume to scholarship domain
"""
        raw = generate_with_groq(prompt)
        return safe_resume_structured(raw)

    else:  # Cover Letter / SOP / Email
        prompt = f"""
You are an expert scholarship application writer.

DOCUMENT TYPE: {doc_type}

APPLICANT RESUME:
-----------------
{resume_text}

SCHOLARSHIP DETAILS:
-------------------
{json.dumps(cleaned_json, indent=2)}

TASK: Generate ONLY JSON with this schema:
{{
  "content": ""
}}

RULES:
- JSON ONLY
- Tailor the {doc_type} content to the scholarship and applicant
- Do not include markdown or extra text
"""
        raw = generate_with_groq(prompt)
        return safe_document_output(raw)

# ---------------------- Parse Scholarship JSON ----------------------
async def parse_scholarship_json(scholarship_json: str) -> dict:
    try:
        return json.loads(scholarship_json)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid scholarship JSON: {str(e)}")

# ---------------------- Endpoints ----------------------
@app.post("/generate/resume", response_model=ResumeStructured)
async def generate_resume(scholarship_json: str = Form(...), resume_file: UploadFile = File(...)):
    cleaned = await parse_scholarship_json(scholarship_json)
    return await handle_generation(cleaned, resume_file, "Enhanced Resume")

@app.post("/generate/coverletter", response_model=DocumentOutput)
async def generate_coverletter(scholarship_json: str = Form(...), resume_file: UploadFile = File(...)):
    cleaned = await parse_scholarship_json(scholarship_json)
    return await handle_generation(cleaned, resume_file, "Cover Letter")

@app.post("/generate/sop", response_model=DocumentOutput)
async def generate_sop(scholarship_json: str = Form(...), resume_file: UploadFile = File(...)):
    cleaned = await parse_scholarship_json(scholarship_json)
    return await handle_generation(cleaned, resume_file, "SOP Document")

@app.post("/generate/email", response_model=DocumentOutput)
async def generate_email(scholarship_json: str = Form(...), resume_file: UploadFile = File(...)):
    cleaned = await parse_scholarship_json(scholarship_json)
    return await handle_generation(cleaned, resume_file, "Scholarship Email")

