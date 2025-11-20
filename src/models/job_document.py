from typing import List
from pydantic import BaseModel


class Input(BaseModel):
    handler: str
    args: List[str]
    path: str


class Action(BaseModel):
    name: str
    type: str
    input: Input
    runAsUser: str


class Step(BaseModel):
    action: Action


class Job(BaseModel):
    version: str
    steps: List[Step]
