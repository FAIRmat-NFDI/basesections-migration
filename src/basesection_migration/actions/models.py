"""Inputs and results shared by the BaseSection migration action."""

from typing import Literal

from pydantic import BaseModel, Field


class EntryRef(BaseModel):
    entry_id: str
    upload_id: str


class MigrationActionInput(BaseModel):
    user_id: str = Field(description='ID of the user starting the action.')
    upload_id: str = Field(description='ID of the administrator-owned report upload.')


class FindEntriesInput(BaseModel):
    user_id: str


class TransformUploadInput(BaseModel):
    target_upload_id: str
    entry_ids: list[str]


class TransformEntryInput(BaseModel):
    target_upload_id: str
    entry_id: str


class TransformEntryResult(BaseModel):
    entry_ref: EntryRef
    mainfile: str | None = None
    status: Literal['transformed', 'skipped', 'failed']
    reason: str | None = None


class TransformUploadResult(BaseModel):
    transformed: list[EntryRef] = Field(default_factory=list)
    skipped: list[TransformEntryResult] = Field(default_factory=list)
    failed: list[TransformEntryResult] = Field(default_factory=list)


class ReportInput(BaseModel):
    user_id: str
    upload_id: str
    workflow_id: str
    started_at: str
    found: list[EntryRef]
    transformed: list[EntryRef]
    skipped: list[TransformEntryResult]
    failed: list[TransformEntryResult]
    error: str | None = None


class MigrationActionOutput(BaseModel):
    found_count: int
    transformed: list[EntryRef]
    skipped_count: int
    failed_count: int
    report_path: str
