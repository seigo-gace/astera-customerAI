from __future__ import annotations
from datetime import UTC, datetime
from pydantic import BaseModel
class ModelManifest(BaseModel):
    base_model_id:str; base_revision:str; training_dataset_hash:str; training_code_revision:str; adapter_mode:str; created_at:str; evaluation_report_hash:str|None=None
    @classmethod
    def create(cls,*,base_model_id:str,base_revision:str,training_dataset_hash:str,training_code_revision:str,adapter_mode:str):
        if not all([base_model_id,base_revision,training_dataset_hash,training_code_revision,adapter_mode]): raise ValueError("model_manifest_fields_required")
        return cls(base_model_id=base_model_id,base_revision=base_revision,training_dataset_hash=training_dataset_hash,training_code_revision=training_code_revision,adapter_mode=adapter_mode,created_at=datetime.now(UTC).isoformat())
