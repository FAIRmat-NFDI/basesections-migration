"""Inputs and results shared by the BaseSection migration action."""

from typing import Literal

from pydantic import BaseModel, Field


class EntryRef(BaseModel):
    entry_id: str
    upload_id: str


class MigrationEntry(BaseModel):
    entry_ref: EntryRef
    mainfile: str
    mainfile_os_path: str


class MigrationActionInput(BaseModel):
    user_id: str = Field(description='ID of the administrator starting the action.')
    upload_id: str = Field(description='ID of the administrator-owned report upload.')
    target_upload_ids: list[str] = Field(
        default_factory=list,
        description='IDs of the upload to migrate. If empty, all available staging '
        'uploads will be migrated.',
    )


class FindEntriesInput(BaseModel):
    user_id: str
    target_upload_ids: list[str] = Field(default_factory=list)


class TransformUploadInput(BaseModel):
    target_upload_id: str = Field(
        description='ID of the upload containing the entries to transform.'
    )
    entries: list[MigrationEntry] = Field(
        description='Entries and resolved mainfile paths to transform.'
    )


class TransformEntryInput(BaseModel):
    mainfile_os_path: str = Field(
        description='Path to the mainfile on the local filesystem.'
    )


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
