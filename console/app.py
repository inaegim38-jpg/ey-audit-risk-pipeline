import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import db

BASE = Path(__file__).parent
app = FastAPI(title="Aegis-Guard Audit Control Room", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
DISPOSITIONS = ["OPEN", "IN_REVIEW", "EXPLAINED", "ESCALATED", "FALSE_POSITIVE"]

def context(request: Request, **kwargs):
    return {"request": request, "app_name": "Aegis-Guard", "engagement": "ENG-2026-001", **kwargs}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return RedirectResponse("/findings", status_code=303)

@app.get("/findings", response_class=HTMLResponse)
def findings_page(request: Request, severity: str | None = None,
                  risk_domain: str | None = None, disposition: str | None = None):
    findings = db.get_findings(severity, risk_domain, disposition)
    return templates.TemplateResponse("findings.html", context(
        request, findings=findings, severity=severity, risk_domain=risk_domain,
        disposition=disposition, severities=SEVERITIES, dispositions=DISPOSITIONS,
        domains=sorted({row["risk_domain"] for row in db.get_findings(limit=500)})))

@app.get("/findings/{finding_id}", response_class=HTMLResponse)
def finding_page(request: Request, finding_id: str):
    finding = db.get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    return templates.TemplateResponse("finding.html", context(
        request, finding=finding, dispositions=DISPOSITIONS))

@app.post("/findings/{finding_id}/disposition")
def disposition_form(finding_id: str, disposition: Annotated[str, Form()],
                     reviewer_id: Annotated[str, Form()] = "demo.reviewer"):
    result = db.update_disposition(finding_id, disposition, reviewer_id)
    if not result:
        raise HTTPException(404, "Finding not found")
    return RedirectResponse(f"/findings/{finding_id}?updated=1", status_code=303)

@app.get("/coverage", response_class=HTMLResponse)
def coverage_page(request: Request):
    return templates.TemplateResponse("coverage.html", context(
        request, coverage=db.get_coverage(), unlinked=db.get_unlinked_findings()))

@app.get("/disclosures", response_class=HTMLResponse)
def disclosure_page(request: Request):
    return templates.TemplateResponse("disclosures.html", context(
        request, disclosures=db.get_disclosures()))

class DispositionRequest(BaseModel):
    disposition: str
    reviewer_id: str = "api.reviewer"

@app.get("/health")
def health():
    db.fetch_one("SELECT 1 AS ok")
    return {"status": "ok", "service": "aegis-console"}

@app.get("/api/findings")
def api_findings(severity: str | None = None, risk_domain: str | None = None,
                disposition: str | None = None, limit: int = 100):
    return db.get_findings(severity, risk_domain, disposition, limit)

@app.get("/api/findings/{finding_id}")
def api_finding(finding_id: str):
    result = db.get_finding(finding_id)
    if not result:
        raise HTTPException(404, "Finding not found")
    return result

@app.patch("/api/findings/{finding_id}/disposition")
def api_disposition(finding_id: str, payload: DispositionRequest):
    try:
        result = db.update_disposition(finding_id, payload.disposition, payload.reviewer_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Finding not found")
    return result

@app.get("/api/evidence/{evidence_id}")
def api_evidence(evidence_id: str):
    result = db.get_evidence(evidence_id)
    if not result:
        raise HTTPException(404, "Evidence not found")
    return result

@app.get("/api/coverage")
def api_coverage():
    return {"coverage": db.get_coverage(), "unlinked": db.get_unlinked_findings()}

@app.get("/api/disclosures")
def api_disclosures():
    return db.get_disclosures()
