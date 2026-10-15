"""Writes infra/pipeline.template.json, the file you upload once in the CloudFormation console.

Run after changing stacks/pipeline_stack.py:  python infra/export_pipeline_template.py
"""

import json
import sys
from pathlib import Path

import aws_cdk as cdk

sys.path.insert(0, str(Path(__file__).parent))
from stacks.pipeline_stack import PipelineStack  # noqa: E402

app = cdk.App(analytics_reporting=False)
stack = PipelineStack(
    app,
    "PersonalAgentPipeline",
    synthesizer=cdk.DefaultStackSynthesizer(generate_bootstrap_version_rule=False),
    env=cdk.Environment(region="us-east-1"),
)
template = app.synth().get_stack_by_name("PersonalAgentPipeline").template
out = Path(__file__).parent / "pipeline.template.json"
out.write_text(json.dumps(template, indent=2) + "\n")
print(f"wrote {out}")
