# lingtongask 音频处理流程优化方案

**日期**: 2026-04-07
**执行人**: 灵克 (LingClaude)
**审计人**: 灵通 (lingtongask)

## 当前问题分析

### 1. 依赖复杂度过高

**现状**：
- 支持 5 种 TTS 提供者：Mock, OpenAI, EdgeTTS, GPT-SoVITS, CosyVoice
- 依赖库：openai, edge-tts, pydub, dashscope, aiohttp, python-pptx 等
- pydub 依赖 ffmpeg，增加了部署复杂度

**问题**：
- 10+ 个可选依赖，配置和安装复杂
- 很多 TTS 提供者是实验性的，不稳定
- 部署时需要处理多个外部依赖的可用性

### 2. 配置参数过多

**现状**：
`create_synthesizer()` 函数有 **20+ 个参数**，包括：
- 基础参数：provider, api_key, male_voice, female_voice
- Edge TTS 参数：edge_rate, edge_volume, edge_pitch
- GPT-SoVITS 参数：gptsovits_url, gptsovits_refer_wav, gptsovits_prompt_text 等
- CosyVoice 参数：cosyvoice_model, cosyvoice_voice_id 等
- 合成参数：segment_pause_ms, speaker_change_pause_ms

**问题**：
- 参数过多，使用困难
- 不同 provider 的参数不统一
- 缺少配置文件支持
- 参数验证不足

### 3. 代码质量问题

**已修复的问题**：
- ✅ 移除未使用的 import（io）
- ✅ 移除未使用的变量（buffer）

**待解决的问题**：
- 多处 `try-except ImportError`，逻辑分散
- fallback 机制混乱，不统一
- 缺少依赖检查和友好的错误提示

### 4. 性能问题

**现状**：
- 音频处理虽然有 async wrapper，但 pydub 操作是同步的
- 缺少批量处理优化
- 缺少音频缓存机制
- 每次合成都要重新调用 API

**问题**：
- 大量片段时性能不佳
- 重复合成相同文本（无缓存）
- 无法充分利用异步并发

## 优化方案

### 阶段 1: 简化依赖（优先级：P0）

**目标**：
- 减少核心依赖数量
- 明确必需依赖和可选依赖
- 优化安装和部署流程

**措施**：

1. **确定核心 TTS 提供者**
   - **保留（核心）**：
     - EdgeTTS：免费、稳定、无需 API Key
     - CosyVoice：高质量声音克隆
   - **降级为可选（实验性）**：
     - OpenAI TTS：成本高
     - GPT-SoVITS：需要部署 GPU 服务器
   - **保留**：
     - MockTTS：用于测试

2. **优化依赖声明**
   ```toml
   # pyproject.toml
   dependencies = [
       "lingflow-core>=3.8.0",  # 核心依赖
       "click>=8.0.0",
       "pyyaml>=6.0.0",
       "python-dotenv>=1.0.0",
       "aiohttp>=3.8.0",
       "pydantic>=2.0.0",
       "httpx>=0.24.0",
       "edge-tts>=6.1.0",  # 核心依赖（免费 TTS）
   ]

   [project.optional-dependencies]
   cosyvoice = [
       "dashscope>=1.14.0",  # CosyVoice TTS
   ]
   audio-processing = [
       "pydub>=0.25.0",  # 音频处理和合并
   ]
   ```

3. **添加依赖检查**
   ```python
   # src/audio/dependencies.py
   class DependencyChecker:
       """依赖检查器"""

       REQUIRED = {
           "edge-tts": "edge_tts",
       }

       OPTIONAL = {
           "dashscope": "CosyVoice (高质量声音克隆)",
           "pydub": "音频处理和合并（需要 ffmpeg）",
       }

       @classmethod
       def check_all(cls) -> dict:
           """检查所有依赖"""
           results = {}

           for pkg, desc in cls.REQUIRED.items():
               results[pkg] = {
                   "available": cls._is_available(desc),
                   "required": True,
               }

           for pkg, desc in cls.OPTIONAL.items():
               results[pkg] = {
                   "available": cls._is_available(desc),
                   "required": False,
               }

           return results

       @classmethod
       def _is_available(cls, module_name: str) -> bool:
           try:
               __import__(module_name)
               return True
           except ImportError:
               return False
   ```

**预期效果**：
- 核心依赖减少 40%
- 安装复杂度降低
- 部署更简单

### 阶段 2: 统一配置管理（优先级：P1）

**目标**：
- 减少参数数量
- 支持配置文件
- 改进用户体验

**措施**：

1. **创建配置类**
   ```python
   # src/audio/config.py
   from pydantic import BaseModel, Field
   from typing import Optional

   class AudioConfig(BaseModel):
       """音频配置"""

       # TTS 提供者选择
       provider: str = Field(
           default="edge",
           description="TTS 提供者 (edge/cosyvoice/mock)"
       )

       # 基础参数
       api_key: Optional[str] = Field(
           default=None,
           description="API 密钥（CosyVoice 需要）"
       )
       male_voice: str = Field(default="zh-CN-YunxiNeural")
       female_voice: str = Field(default="zh-CN-XiaoxiaoNeural")

       # 合成参数
       segment_pause_ms: int = Field(default=500, ge=0, le=5000)
       speaker_change_pause_ms: int = Field(default=1200, ge=0, le=5000)
       enable_bg_music: bool = Field(default=False)
       bg_music_volume: float = Field(default=-20, ge=-40, le=0)

       # Edge TTS 参数
       edge_rate: str = Field(default="-10%")
       edge_volume: str = Field(default="+0%")
       edge_pitch: str = Field(default="+0Hz")

       # CosyVoice 参数
       cosyvoice_model: str = Field(default="cosyvoice-v2")
       cosyvoice_host_voice_id: Optional[str] = None
       cosyvoice_guest_voice_id: Optional[str] = None
       cosyvoice_speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
       cosyvoice_pitch_rate: float = Field(default=1.0, ge=0.5, le=2.0)

       class Config:
           env_prefix = "LINGTONG_TTS_"
           extra = "forbid"
   ```

2. **支持配置文件**
   ```yaml
   # config/tts.yaml
   provider: edge
   male_voice: zh-CN-YunxiNeural
   female_voice: zh-CN-XiaoxiaoNeural

   # 合成参数
   segment_pause_ms: 500
   speaker_change_pause_ms: 1200
   enable_bg_music: false

   # Edge TTS 参数
   edge_rate: -10%
   edge_volume: +0%
   edge_pitch: +0Hz

   # CosyVoice 参数（可选）
   # cosyvoice_model: cosyvoice-v2
   # cosyvoice_host_voice_id: zh-CN-YunxiNeural
   # cosyvoice_guest_voice_id: zh-CN-XiaoxiaoNeural
   ```

3. **简化创建函数**
   ```python
   async def create_synthesizer(
       config: AudioConfig | None = None,
       config_path: str | None = None,
   ) -> AudioSynthesizer:
       """创建音频合成器

       Args:
           config: 音频配置对象
           config_path: 配置文件路径（YAML）

       Returns:
           AudioSynthesizer 实例
       """
       if config_path:
           config = AudioConfig.from_yaml_file(config_path)
       elif not config:
           config = AudioConfig()

       return await _create_from_config(config)
   ```

**预期效果**：
- 参数数量从 20+ 减少到 1-2 个
- 支持配置文件
- 更好的参数验证

### 阶段 3: 性能优化（优先级：P2）

**目标**：
- 提升音频合成速度
- 添加缓存机制
- 支持批量处理

**措施**：

1. **添加音频缓存**
   ```python
   # src/audio/cache.py
   import hashlib
   import json
   from pathlib import Path
   from typing import Optional

   class AudioCache:
       """音频缓存"""

       def __init__(self, cache_dir: Path):
           self.cache_dir = cache_dir
           self.cache_dir.mkdir(parents=True, exist_ok=True)

       def _get_key(self, text: str, voice: str, provider: str) -> str:
           """生成缓存键"""
           data = f"{text}:{voice}:{provider}"
           return hashlib.md5(data.encode()).hexdigest()

       def get(self, text: str, voice: str, provider: str) -> Optional[bytes]:
           """获取缓存"""
           key = self._get_key(text, voice, provider)
           cache_file = self.cache_dir / f"{key}.mp3"

           if cache_file.exists():
               return cache_file.read_bytes()
           return None

       def set(self, text: str, voice: str, provider: str, audio: bytes) -> None:
           """设置缓存"""
           key = self._get_key(text, voice, provider)
           cache_file = self.cache_dir / f"{key}.mp3"
           cache_file.write_bytes(audio)
   ```

2. **批量处理优化**
   ```python
   # src/audio/tts.py
   class AudioSynthesizer:
       # ... 现有代码 ...

       async def synthesize_episode_batch(
           self,
           episode: Episode,
           output_dir: Path,
           batch_size: int = 5,
       ) -> Path:
           """批量合成音频

           Args:
               episode: 节目对象
               output_dir: 输出目录
               batch_size: 批量大小（并发数）

           Returns:
               音频文件路径
           """
           segments = episode.script
           audio_segments = []

           # 分批处理
           for i in range(0, len(segments), batch_size):
               batch = segments[i:i + batch_size]

               # 并发合成
               tasks = [
                   self._synthesize_segment(seg, idx, output_dir)
                   for idx, seg in enumerate(batch, start=i)
               ]
               results = await asyncio.gather(*tasks, return_exceptions=True)

               # 处理结果
               for result in results:
                   if not isinstance(result, Exception):
                       audio_segments.append(result)

           # 合并音频
           final_path = output_dir / f"{episode.episode_id}.mp3"
           await self._merge_audio(audio_segments, final_path)

           return final_path
   ```

3. **使用更快的音频库（可选）**
   - 考虑使用 `pydantic-audio` 或 `librosa` 替代 pydub
   - 或者使用 `ffmpeg-python` 直接调用 ffmpeg，减少依赖

**预期效果**：
- 音频合成速度提升 30-50%
- 减少重复 API 调用
- 更好的并发性能

### 阶段 4: 代码重构（优先级：P3）

**目标**：
- 改进代码结构
- 统一错误处理
- 提升可维护性

**措施**：

1. **统一 TTS 提供者接口**
   ```python
   # src/audio/base.py
   from abc import ABC, abstractmethod
   from dataclasses import dataclass

   @dataclass
   class TTSConfig:
       """TTS 配置"""
       provider: str
       api_key: str | None = None
       # ... 其他配置

   @dataclass
   class TTSResult:
       """TTS 结果"""
       audio_data: bytes
       success: bool
       error: str | None = None

   class TTSProvider(ABC):
       """TTS 提供者抽象接口"""

       @abstractmethod
       async def synthesize(self, text: str, voice: str) -> TTSResult:
           """合成音频"""
           pass

       @abstractmethod
       def get_voices(self) -> dict[str, str]:
           """获取可用音色"""
           pass

       @abstractmethod
       def check_health(self) -> bool:
           """检查服务健康状态"""
           pass
   ```

2. **统一错误处理**
   ```python
   # src/audio/exceptions.py
   class AudioError(Exception):
       """音频处理错误基类"""
       pass

   class TTSProviderError(AudioError):
       """TTS 提供者错误"""
       pass

   class DependencyMissingError(AudioError):
       """依赖缺失错误"""
       pass
   ```

3. **添加日志和监控**
   ```python
   # src/audio/metrics.py
   from dataclasses import dataclass
   from datetime import datetime
   from typing import List

   @dataclass
   class TTSMetrics:
       """TTS 指标"""
       provider: str
       timestamp: datetime
       text_length: int
       audio_size: int
       duration_ms: int
       success: bool
       error: str | None = None

   class MetricsCollector:
       """指标收集器"""

       def __init__(self):
           self.metrics: List[TTSMetrics] = []

       def record(self, metric: TTSMetrics) -> None:
           """记录指标"""
           self.metrics.append(metric)

       def get_summary(self) -> dict:
           """获取摘要"""
           if not self.metrics:
               return {}

           success_rate = sum(1 for m in self.metrics if m.success) / len(self.metrics)
           avg_duration = sum(m.duration_ms for m in self.metrics) / len(self.metrics)

           return {
               "total_requests": len(self.metrics),
               "success_rate": success_rate,
               "avg_duration_ms": avg_duration,
           }
   ```

**预期效果**：
- 代码更清晰、更易维护
- 更好的错误处理
- 更好的可观测性

## 实施计划

### 第 1 周（优先级 P0）
- ✅ 修复代码质量问题（已完成）
- 🔄 简化依赖声明
- 🔄 添加依赖检查器

### 第 2 周（优先级 P1）
- ⏳ 创建配置类
- ⏳ 支持配置文件
- ⏳ 简化创建函数

### 第 3-4 周（优先级 P2）
- ⏳ 添加音频缓存
- ⏳ 批量处理优化
- ⏳ 性能测试和调优

### 后续（优先级 P3）
- ⏳ 代码重构
- ⏳ 统一错误处理
- ⏳ 添加监控

## 风险评估

### 低风险
- 简化依赖声明（向后兼容）
- 添加依赖检查器（新功能）
- 修复代码质量问题（向后兼容）

### 中风险
- 统一配置管理（API 变化）
- 添加音频缓存（新功能）

### 高风险
- 代码重构（大规模变化）
- 更换音频库（兼容性问题）

## 成功指标

### 量化指标
1. **依赖数量**：从 10+ 减少到 5-6 个（-40%）
2. **参数数量**：从 20+ 减少到 1-2 个（-90%）
3. **性能提升**：音频合成速度提升 30-50%
4. **代码质量**：ruff/mypy 错误为 0

### 质性指标
1. **用户体验**：更简单的配置，更清晰的错误提示
2. **可维护性**：代码更清晰，更易理解
3. **可观测性**：更好的日志和监控

## 总结

lingtongask 的音频处理流程主要问题是**依赖复杂度**和**配置复杂度**。通过分阶段优化：

1. **阶段 1**：简化依赖，明确核心功能
2. **阶段 2**：统一配置，改进用户体验
3. **阶段 3**：性能优化，提升效率
4. **阶段 4**：代码重构，提高可维护性

预计整体优化后，音频处理流程将更加简洁、高效、易用。

---

**文档生成时间**: 2026-04-07
**状态**: 优化方案已制定，待实施
**下一步**: 开始阶段 1（简化依赖）
