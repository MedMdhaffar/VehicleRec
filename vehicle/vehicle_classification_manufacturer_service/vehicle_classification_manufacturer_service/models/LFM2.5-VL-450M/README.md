---
library_name: transformers
license: other
license_name: lfm1.0
license_link: LICENSE
language:
- en
- ja
- ko
- fr
- es
- de
- ar
- zh
- pt
pipeline_tag: image-text-to-text
tags:
- liquid
- lfm2
- lfm2-vl
- edge
- lfm2.5-vl
- lfm2.5
base_model: LiquidAI/LFM2.5-350M
---

<center>
<div style="text-align: center;">
  <img 
    src="https://cdn-uploads.huggingface.co/production/uploads/61b8e2ba285851687028d395/2b08LKpev0DNEk6DlnWkY.png" 
    alt="Liquid AI"
    style="width: 100%; max-width: 100%; height: auto; display: inline-block; margin-bottom: 0.5em; margin-top: 0.5em;"
  />
</div>
<div style="display: flex; justify-content: center; gap: 0.5em;">
<a href="https://playground.liquid.ai/chat?model=lfm2.5-vl-450m"><strong>Try LFM</strong></a> • <a href="https://docs.liquid.ai/lfm/getting-started/welcome"><strong>Docs</strong></a> • <a href="https://leap.liquid.ai/"><strong>LEAP</strong></a> • <a href="https://discord.com/invite/liquid-ai"><strong>Discord</strong></a>
</div>
</center>

<br>

# LFM2.5‑VL-450M

LFM2.5‑VL-450M is [Liquid AI](https://www.liquid.ai/)'s refreshed version of the first vision-language model, [LFM2-VL-450M](https://huggingface.co/LiquidAI/LFM2-VL-450M), built on an updated backbone [LFM2.5-350M](https://huggingface.co/LiquidAI/LFM2.5-350M) and tuned for stronger real-world performance. Find more about LFM2.5 family of models in our [blog post](http://www.liquid.ai/blog/lfm2-5-vl-450m).

* **Enhanced instruction following** on vision and language tasks.
* **Improved multilingual vision understanding** in Arabic, Chinese, French, German, Japanese, Korean, Portuguese and Spanish.
* **Bounding box prediction and object detection** for grounded visual understanding.
* **Function calling support** for text-only input.
  
🎥⚡️ You can try LFM2.5-VL-450M running locally in your browser with our real-time video stream captioning [WebGPU demo](https://huggingface.co/spaces/LiquidAI/LFM2.5-VL-450M-WebGPU) 🎥⚡️ 

Alternatively, try the API model on the [Playground](https://playground.liquid.ai/chat?model=lfm2.5-vl-450m).

## 📄 Model details

LFM2.5-VL-450M is a general-purpose vision-language model with the following features:

- **LM Backbone**: LFM2.5-350M
- **Vision encoder**: SigLIP2 NaFlex shape‑optimized 86M
- **Context length**: 32,768 tokens
- **Vocabulary size**: 65,536
- **Languages**: English, Arabic, Chinese, French, German, Japanese, Korean, Portuguese, and Spanish
- **Native resolution processing**: handles images up to 512*512 pixels without upscaling and preserves non-standard aspect ratios without distortion
- **Tiling strategy**: splits large images into non-overlapping 512×512 patches and includes thumbnail encoding for global context
- **Inference-time flexibility**: user-tunable maximum image tokens and tile count for speed/quality tradeoff without retraining
- **Generation parameters**: 
  - text: `temperature=0.1`, `min_p=0.15`, `repetition_penalty=1.05`
  - vision: `min_image_tokens=32` `max_image_tokens=256`, `do_image_splitting=True`

| Model | Description |
|-------|-------------|
| [**LFM2.5-VL-450M**](https://huggingface.co/LiquidAI/LFM2.5-VL-450M) | Original model checkpoint in native format. Best for fine-tuning or inference with Transformers and vLLM. |
| [LFM2.5-VL-450M-GGUF](https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF) | Quantized format for llama.cpp and compatible tools. Optimized for CPU inference and local deployment with reduced memory usage. |
| [LFM2.5-VL-450M-ONNX](https://huggingface.co/LiquidAI/LFM2.5-VL-450M-ONNX) | ONNX Runtime format for cross-platform deployment. Enables hardware-accelerated inference across diverse environments (cloud, edge, mobile). |
| [LFM2.5-VL-450M-MLX-8bit](https://huggingface.co/LiquidAI/LFM2.5-VL-450M-MLX-8bit) | MLX format for Apple Silicon. Optimized for fast on-device inference on Mac with [mlx-vlm](https://github.com/Blaizzy/mlx-vlm). Also available in [4bit](https://huggingface.co/LiquidAI/LFM2.5-VL-450M-MLX-4bit), [5bit](https://huggingface.co/LiquidAI/LFM2.5-VL-450M-MLX-5bit), [6bit](https://huggingface.co/LiquidAI/LFM2.5-VL-450M-MLX-6bit), and [bf16](https://huggingface.co/LiquidAI/LFM2.5-VL-450M-MLX-bf16). |

We recommend using it for general vision-language workloads, captioning and object detection. It’s not well-suited for knowledge-intensive tasks or fine-grained OCR.

### Chat Template

LFM2.5-VL uses a ChatML-like format. See the [Chat Template documentation](https://docs.liquid.ai/lfm/key-concepts/chat-template#vision-models) for details.

```
<|startoftext|><|im_start|>system
You are a helpful multimodal assistant by Liquid AI.<|im_end|>
<|im_start|>user
<image>Describe this image.<|im_end|>
<|im_start|>assistant
This image shows a Caenorhabditis elegans (C. elegans) nematode.<|im_end|>
```

You can use [`processor.apply_chat_template()`](https://huggingface.co/docs/transformers/en/chat_templating_multimodal) to format your messages automatically.

## 🏃 Inference

You can run LFM2.5-VL-450M with Hugging Face [`transformers`](https://github.com/huggingface/transformers) v5.1 or newer:

```bash
pip install transformers pillow
```

```python
from transformers import AutoProcessor, AutoModelForImageTextToText
from transformers.image_utils import load_image

# Load model and processor
model_id = "LiquidAI/LFM2.5-VL-450M"
model = AutoModelForImageTextToText.from_pretrained(
    model_id,
    device_map="auto",
    dtype="bfloat16"
)
processor = AutoProcessor.from_pretrained(model_id)

# Load image and create conversation
url = "https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg"
image = load_image(url)
conversation = [
    {
        "role": "user",
        "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": "What is in this image?"},
        ],
    },
]

# Generate Answer
inputs = processor.apply_chat_template(
    conversation,
    add_generation_prompt=True,
    return_tensors="pt",
    return_dict=True,
    tokenize=True,
).to(model.device)
outputs = model.generate(**inputs, max_new_tokens=64)
processor.batch_decode(outputs, skip_special_tokens=True)[0]

# This image captures the iconic Statue of Liberty standing majestically on Liberty Island in New York City. The statue, a symbol of freedom and democracy, is prominently featured in the foreground, its greenish-gray hue contrasting beautifully with the surrounding water.
```

### Visual grounding

LFM2.5-VL-450M supports bounding box prediction:

```python
url = "https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg"
image = load_image(url)
query = "status"
prompt = f'Detect all instances of: {query}. Response must be a JSON array: [{"label": ..., "bbox": [x1, y1, x2, y2]}, ...]. Coordinates are normalized to [0,1].'

conversation = [
    {
        "role": "user",
        "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": prompt},
        ],
    },
]

# Generate Answer
inputs = processor.apply_chat_template(
    conversation,
    add_generation_prompt=True,
    return_tensors="pt",
    return_dict=True,
    tokenize=True,
).to(model.device)
outputs = model.generate(**inputs, max_new_tokens=64)
processor.batch_decode(outputs, skip_special_tokens=True)[0]

# [{"label": "statue", "bbox": [0.3, 0.25, 0.4, 0.65]}]
```

### Tool Use

LFM2.5 supports function calling for text only input by applying the chat template with the tokenizer. See the [Tool Use documentation](https://docs.liquid.ai/lfm/key-concepts/tool-use) for the full guide.

```python
tools = [{
    "name": "get_weather",
    "description": "Get current weather for a location",
    "parameters": {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"]
    }
}]

messages = [{"role": "user", "content": "What's the weather in Paris?"}]

# Apply chat template with tools
inputs = processor.tokenizer.apply_chat_template(
    messages,
    tools=tools,
    add_generation_prompt=True,
    return_tensors="pt",
    return_dict=True,
)
input_ids = inputs["input_ids"].to(model.device)
outputs = model.generate(input_ids, max_new_tokens=256)
response = processor.tokenizer.decode(outputs[0, input_ids.shape[1]:], skip_special_tokens=False)

# <|tool_call_start|>[get_weather(location="Paris")]<|tool_call_end|>I am retrieving the current weather for Paris.<|im_end|>
```

| Name | Description | Docs | Notebook |
|------|-------------|------|----------|
| [Transformers](https://github.com/huggingface/transformers) | Simple inference with direct access to model internals. | <a href="https://docs.liquid.ai/lfm/inference/transformers#vision-models">Link</a>| <a href="https://colab.research.google.com/drive/1WVQpf4XrHgHFkP0FnlZfx2nK8PugvQNZ?usp=sharing"><img src="https://cdn-uploads.huggingface.co/production/uploads/61b8e2ba285851687028d395/vlOyMEjwHa_b_LXysEu2E.png" width="110" alt="Colab link"></a> |
| [vLLM](https://github.com/vllm-project/vllm) | High-throughput production deployments with GPU. | <a href="https://docs.liquid.ai/deployment/gpu-inference/vllm#vision-models">Link</a> | <a href="https://colab.research.google.com/drive/1sUfQlqAvuAVB4bZ6akYVQPGmHtTDUNpF?usp=sharing"><img src="https://cdn-uploads.huggingface.co/production/uploads/61b8e2ba285851687028d395/vlOyMEjwHa_b_LXysEu2E.png" width="110" alt="Colab link"></a> |
| [SGLang](https://github.com/vllm-project/vllm) | High-throughput production deployments with GPU. | <a href="https://docs.liquid.ai/deployment/gpu-inference/sglang#vision-models">Link</a> | <a href="https://colab.research.google.com/drive/1qJlAFag223yFOZGzuMIkYUFhybM9ao5g?usp=sharing"><img src="https://cdn-uploads.huggingface.co/production/uploads/61b8e2ba285851687028d395/vlOyMEjwHa_b_LXysEu2E.png" width="110" alt="Colab link"></a> |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Cross-platform inference with CPU offloading. | <a href="https://docs.liquid.ai/lfm/inference/llama-cpp#vision-models">Link</a> | <a href="https://colab.research.google.com/drive/1q2PjE6O_AahakRlkTNJGYL32MsdUcj7b?usp=sharing"><img src="https://cdn-uploads.huggingface.co/production/uploads/61b8e2ba285851687028d395/vlOyMEjwHa_b_LXysEu2E.png" width="110" alt="Colab link"></a> |

## 🔧 Fine-tuning

We recommend fine-tuning LFM2.5-VL-450M model on your use cases to maximize performance.

| Notebook  | Description                                                          | Link |
|-----------|----------------------------------------------------------------------|------|
| SFT (Unsloth) | Supervised Fine-Tuning with LoRA using Unsloth. | <a href="https://colab.research.google.com/drive/1FaR2HSe91YDe88TG97-JVxMygl-rL6vB?usp=sharing"><img src="https://cdn-uploads.huggingface.co/production/uploads/61b8e2ba285851687028d395/vlOyMEjwHa_b_LXysEu2E.png" width="110" alt="Colab link"></a> |
| SFT (TRL) | Supervised Fine-Tuning with LoRA using TRL. | <a href="https://colab.research.google.com/drive/10530_jt_Joa5zH2wgYlyXosypq1R7PIz?usp=sharing"><img src="https://cdn-uploads.huggingface.co/production/uploads/61b8e2ba285851687028d395/vlOyMEjwHa_b_LXysEu2E.png" width="110" alt="Colab link"></a> |


## 📊 Performance

LFM2.5-VL-450M improves over LFM2-VL-450M across both vision and language benchmarks, while also adding two new capabilities: bounding box prediction on RefCOCO-M and function calling support measured by BFCLv4.

### Vision benchmarks

| Model              | MMStar | RealWorldQA | MMBench (dev en) | MMMU (val) | POPE | MMVet | BLINK | InfoVQA (val) | OCRBench | MM-IFEval | MMMB | CountBench | RefCOCO-M |
|--------------------|--------|-------------|------------------|------------|------|-------|-------|---------------|----------|------------|------|------------|-----------|
| **LFM2.5-VL-450M** | 43.00  | 58.43       | 60.91            | 32.67      | 86.93| 41.10 | 43.92 | 43.02         | 684      | 45.00      | 68.09| 73.31      | 81.28     |
| LFM2-VL-450M       | 40.87  | 52.03       | 56.27            | 34.44      | 83.79| 33.85 | 42.61 | 44.56         | 657      | 33.09      | 54.29| 47.64      | -         |
| SmolVLM2-500M      | 38.20  | 49.90       | 52.32            | 34.10      | 82.67| 29.90 | 40.70 | 24.64         | 609      | 11.27      | 46.79| 61.81      | -         |

All vision benchmark scores are obtained using [VLMEvalKit](https://github.com/open-compass/VLMEvalKit). Multilingual scores are based on the average of benchmarks translated by GPT-4.1-mini from English to Arabic, Chinese, French, German, Japanese, Korean, Portuguese, and Spanish.

### Language benchmarks

| Model              | GPQA | MMLU Pro | IFEval | Multi-IF | BFCLv4 |
|--------------------|------|----------|--------|----------|--------|
| **LFM2.5-VL-450M** | 25.66| 19.32    | 61.16  | 34.63    | 21.08  |
| LFM2-VL-450M       | 23.13| 17.22    | 51.75  | 26.21    | -      |
| SmolVLM2-500M      | 23.84| 13.57    | 30.14  | 6.82     | -      |

## 📬 Contact

- Got questions or want to connect? [Join our Discord community](https://discord.com/invite/liquid-ai)
- If you are interested in custom solutions with edge deployment, please contact [our sales team](https://www.liquid.ai/contact).

## Citation

```
@article{liquidai2025lfm2,
 title={LFM2 Technical Report},
 author={Liquid AI},
 journal={arXiv preprint arXiv:2511.23404},
 year={2025}
}
```