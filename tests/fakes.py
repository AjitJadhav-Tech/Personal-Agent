"""A scripted stand-in for the model: answers by the "Task:" line of the system prompt."""

import json


class FakeModel:
    def __init__(self):
        self.calls: list[str] = []
        self.score = 80
        self.resume_bullets = [
            {
                "text": "Designed and deployed an agentic AI platform on Bedrock AgentCore Runtime.",
                "fact_ids": ["nexus-platform"],
            },
            {"text": "Cut manual effort by 70% and costs by 25%.", "fact_ids": ["nexus-results"]},
        ]
        self.unsupported: list[dict] = []
        self.revision: list[dict] | None = None

    def __call__(self, system: str, prompt: str) -> str:
        task = system.split("\n", 1)[0].removeprefix("Task: ").strip()
        self.calls.append(task)
        if task == "score_job":
            return json.dumps(
                {
                    "score": self.score,
                    "must_haves_met": ["LangGraph"],
                    "gaps": ["Kubernetes"],
                    "reason": "Strong agentic AI match.",
                }
            )
        if task == "tailor_resume":
            return (
                "```json\n"
                + json.dumps(
                    {
                        "summary": "Generative AI engineer with 4.5 years of experience.",
                        "summary_fact_ids": ["years", "objective"],
                        "skills": ["LangGraph", "Python", "Rust"],
                        "experience": [{"exp_id": "cg-genai", "bullets": self.resume_bullets}],
                    }
                )
                + "\n```"
            )
        if task == "cover_letter":
            return json.dumps(
                {
                    "paragraphs": [
                        {
                            "text": "I build agentic AI platforms on AWS Bedrock AgentCore.",
                            "fact_ids": ["nexus-platform"],
                        },
                        {"text": "Your team is hiring for this role.", "fact_ids": ["jd"]},
                    ]
                }
            )
        if task == "fact_check":
            out, self.unsupported = self.unsupported, []
            return json.dumps({"unsupported": out})
        if task == "revise_items":
            return json.dumps({"items": self.revision or []})
        return "Draft post about approvals."
