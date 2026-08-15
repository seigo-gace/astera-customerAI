from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SFTTrainingRequest:
    base_model_id: str
    base_revision: str
    output_dir: str
    dataset_path: str
    adapter_mode: str
    lora_r: int | None = None
    lora_alpha: int | None = None
    lora_dropout: float | None = None
    lora_target_modules: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.base_model_id or not self.base_revision:
            raise ValueError("base_model_and_revision_must_be_decided_before_training")
        if not self.output_dir or not self.dataset_path:
            raise ValueError("output_dir_and_dataset_path_required")
        if self.adapter_mode in {"", "benchmark_required"}:
            raise ValueError("adapter_mode_must_be_decided_before_training")
        if self.adapter_mode != "lora":
            raise ValueError(f"unsupported_adapter_mode:{self.adapter_mode}")
        if self.lora_r is None or self.lora_r <= 0:
            raise ValueError("lora_r_must_be_decided_before_training")
        if self.lora_alpha is None or self.lora_alpha <= 0:
            raise ValueError("lora_alpha_must_be_decided_before_training")
        if self.lora_dropout is None or not 0.0 <= self.lora_dropout < 1.0:
            raise ValueError("lora_dropout_must_be_decided_before_training")
        if not self.lora_target_modules or any(not item.strip() for item in self.lora_target_modules):
            raise ValueError("lora_target_modules_must_be_decided_before_training")


def train_sft(request: SFTTrainingRequest) -> None:
    request.validate()
    try:
        from datasets import load_dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer
    except ImportError as error:
        raise RuntimeError("training_dependencies_not_installed") from error

    dataset = load_dataset("json", data_files=request.dataset_path, split="train")
    tokenizer = AutoTokenizer.from_pretrained(
        request.base_model_id,
        revision=request.base_revision,
    )
    model = AutoModelForCausalLM.from_pretrained(
        request.base_model_id,
        revision=request.base_revision,
    )

    peft_config = LoraConfig(
        r=request.lora_r,
        lora_alpha=request.lora_alpha,
        lora_dropout=request.lora_dropout,
        target_modules=list(request.lora_target_modules),
        task_type="CAUSAL_LM",
    )
    config = SFTConfig(output_dir=request.output_dir)
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=peft_config,
    )
    trainer.train()
    trainer.save_model(request.output_dir)
