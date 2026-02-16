from pydantic import BaseModel, Field


class Metadata(BaseModel):
    outpost: str
    group: str
    bucket: str

class Data(BaseModel):
    mission_uuid: str = Field(..., alias="mission_uuid")
    download_url: str
    download_path: str
    metadata: Metadata

    class Config:
        populate_by_name = True

class Job(BaseModel):
    operation: str
    data: Data