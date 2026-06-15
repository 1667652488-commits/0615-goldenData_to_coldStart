#!/usr/bin/env python3
"""
调用 LLM —— 支持内网/外网大模型切换 + 调试日志

内网: http://100.100.135.164:8006
外网: SiliconFlow (https://api.siliconflow.cn)
"""

import json
import time
import requests

# ====== 配置开关：设为 "intranet" 用内网，"internet" 用外网 ======
LLM_MODE = "intranet"  # "intranet" | "internet"

LLM_CONFIGS = {
    "intranet": {
        "base_url": "http://100.100.135.164:8006",
        "token": "no-use",
        "default_model": "DeepSeek-V4-Flash",
        "timeout": 120,
    },
    "internet": {
        "base_url": "https://api.siliconflow.cn",
        "token": "sk-ujjwatckhsqtmptlfzwkazagayqbjosmgknyftutiqdjnfgw",
        "default_model": "deepseek-ai/DeepSeek-V4-Pro",
        "timeout": 120,
    },
}

# 埋点日志文件（设为 None 关闭日志，设为路径开启）
DEBUG_LOG = None


def _log(msg: str):
    """写入调试日志"""
    if DEBUG_LOG:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")


def call_llm(messages: list, model: str = None, stream: bool = False) -> str:
    """
    调用 LLM（自动根据 LLM_MODE 选择内网/外网）

    messages: OpenAI 格式 [{"role": "system"/"user"/"assistant", "content": "..."}, ...]
    model:    模型名称，None 则使用当前模式的默认模型
    返回 LLM 的回复文本
    """
    config = LLM_CONFIGS[LLM_MODE]
    if model is None:
        model = config["default_model"]

    url = config["base_url"].rstrip("/") + "/v1/chat/completions"

    payload = {
        "model": model,
        "messages": messages,
        "stream": stream
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['token']}"
    }

    # 估算输入 token 数
    total_chars = sum(len(m.get("content", "")) for m in messages)
    est_input_tokens = int(total_chars / 1.5)

    _log(f"REQUEST mode={LLM_MODE} model={model} stream={stream} "
         f"est_input_tokens≈{est_input_tokens} timeout={config['timeout']}s")

    t_start = time.time()

    try:
        if stream:
            # 流式：逐行读取 SSE
            resp = requests.post(
                url, json=payload, headers=headers,
                stream=True, timeout=config["timeout"]
            )
            resp.raise_for_status()
            texts = []
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data:"):
                    content = line[5:].strip()
                    if content == "[DONE]":
                        break
                    try:
                        chunk = json.loads(content)
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            texts.append(delta)
                    except json.JSONDecodeError:
                        pass
            output = "".join(texts).strip()
            total_time = time.time() - t_start
            _log(f"  SUCCESS(stream) output_len={len(output)} total={total_time:.2f}s")
            return output

        # 非流式
        resp = requests.post(
            url, json=payload, headers=headers,
            timeout=config["timeout"]
        )
        resp.raise_for_status()
        result = resp.json()
        total_time = time.time() - t_start

        choices = result.get("choices", [])
        if choices:
            output = choices[0].get("message", {}).get("content", "").strip()
            finish_reason = choices[0].get("finish_reason", "")
            _log(f"  SUCCESS finish_reason={finish_reason} "
                 f"output_len={len(output)} total={total_time:.2f}s")
            return output

        _log(f"  WARNING choices为空 response_keys={list(result.keys())}")
        return json.dumps(result, ensure_ascii=False)[:300]

    except requests.exceptions.Timeout:
        elapsed = time.time() - t_start
        _log(f"  TIMEOUT after {elapsed:.2f}s (limit={config['timeout']}s)")
        return f"[LLM 调用超时 ({LLM_MODE}): 服务器在 {config['timeout']}s 内未响应]"

    except requests.exceptions.ConnectionError as e:
        elapsed = time.time() - t_start
        _log(f"  CONNECTION_ERROR after {elapsed:.2f}s: {e}")
        return f"[LLM 连接失败 ({LLM_MODE}): {e}]"

    except requests.exceptions.HTTPError as e:
        elapsed = time.time() - t_start
        _log(f"  HTTP_ERROR after {elapsed:.2f}s: {e}")
        return f"[LLM HTTP 错误 ({LLM_MODE}): {e}]"

    except Exception as e:
        elapsed = time.time() - t_start
        _log(f"  ERROR after {elapsed:.2f}s: {type(e).__name__}: {e}")
        return f"[LLM 调用错误 ({LLM_MODE}): {e}]"


def test_llm():
    """简单测试"""
    config = LLM_CONFIGS[LLM_MODE]
    print(f"当前模式: {LLM_MODE} | 地址: {config['base_url']} | 模型: {config['default_model']}")
    print(f"调试日志: {DEBUG_LOG or '关闭'}")
    messages = [
        {"role": "system", "content": "你是一个想购买银行理财产品的顾客，正在和银行客服对话。"},
        {"role": "user", "content": "请说第一句话，向银行客服询问理财产品。"}
    ]
    reply = call_llm(messages)
    print(f"LLM 回复: {reply}")
    return reply


if __name__ == "__main__":
    test_llm()
