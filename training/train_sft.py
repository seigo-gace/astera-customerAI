from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class SFTTrainingRequest:
    base_model_id:str; base_revision:str; output_dir:str; dataset_path:str; adapter_mode:str
    def validate(self):
        if not self.base_model_id or not self.base_revision: raise ValueError("base_model_and_revision_must_be_decided_before_training")
        if self.adapter_mode in {"","benchmark_required"}: raise ValueError("adapter_mode_must_be_decided_before_training")
def train_sft(request:SFTTrainingRequest)->None:
    request.validate()
    try:
        from datasets import load_dataset
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer
    except ImportError as error: raise RuntimeError("training_dependencies_not_installed") from error
    dataset=load_dataset("json",data_files=request.dataset_path,split="train"); tokenizer=AutoTokenizer.from_pretrained(request.base_model_id,revision=request.base_revision); model=AutoModelForCausalLM.from_pretrained(request.base_model_id,revision=request.base_revision); config=SFTConfig(output_dir=request.output_dir); trainer=SFTTrainer(model=model,args=config,train_dataset=dataset,processing_class=tokenizer); trainer.train(); trainer.save_model(request.output_dir)
