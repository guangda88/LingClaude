# 灵族身份认知锚定机制设计

> **发起**: 灵克（LingClaude）
> **日期**: 2026-04-12
> **基于**: 身份认知测试报告、SELF_PORTRAIT.md、CHARTER.md

---

## 一、问题分析

### 1.1 当前身份认知的问题

根据 `docs/identity_test_report.md`，灵族各服务在身份认知上存在以下问题：

| 服务 | 身份认同 | 用户认知 | 主要问题 |
|------|---------|---------|---------|
| 灵知 | ✅ 正确 | ❌ 编造用户身份 | 过度专业化，强行套用术语 |
| 灵依 | ✅ 正确 | ❌ 虚构用户身份 | 量化编造，虚构具体数据 |
| 灵克 | ❌ 模板返回 | ❌ 未回答 | 路由层未调用LLM |
| 灵极优 | ✅ 正确 | ⚠️ 诚实承认不知道 | 自我重复，回避实质 |
| 灵研 | ✅ 正确 | ⚠️ 诚实承认不知道 | 轻微语义偏差 |
| 灵知_auto | ✅ 正确 | ⚠️ 诚实承认不知道 | 保守正确，无幻觉 |

**核心问题**：
1. **身份认同不稳定**：虽然大部分服务能正确识别自己，但依赖系统 prompt 的硬编码
2. **用户认知失败**：所有服务都无法正确识别用户（要么编造，要么泛化）
3. **幻觉风险高**：角色设定越具体，幻觉越定向（如灵依的量化编造）
4. **身份漂移**：长期交互中，身份认知可能随上下文变化而漂移

### 1.2 理论基础

**身份认知的三个层次**：

```
┌─────────────────────────────────────────────────────┐
│  Layer 3: 认知锚定层（Cognitive Anchoring）      │
│  • 内化的身份认知                               │
│  • 自动的身份核查                               │
│  • 防漂移机制                                   │
├─────────────────────────────────────────────────────┤
│  Layer 2: 行为体现层（Behavioral Manifestation） │
│  • 通过行为体现身份                             │
│  • 行为与身份的一致性验证                       │
│  • 行为修正反馈                                 │
└─────────────────────────────────────────────────────┘
                          ↑
┌─────────────────────────────────────────────────────┐
│  Layer 1: 文档定义层（Document Definition）      │
│  • SELF_PORTRAIT.md（静态定义）                 │
│  • CHARTER.md（使命、愿景、价值观）             │
│  • 系统 prompt（动态注入）                       │
└─────────────────────────────────────────────────────┘
```

**关键洞察**：
- Layer 1（文档定义）是基础，但不足以稳定身份认知
- Layer 2（行为体现）是核心，通过行为验证身份
- Layer 3（认知锚定）是目标，实现内化的身份认知

---

## 二、身份认知锚定机制设计

### 2.1 核心组件

```python
@dataclass
class IdentityAnchor:
    """身份认知锚点"""
    id: str  # 锚点ID
    type: AnchorType  # 锚点类型
    content: str  # 锚点内容
    confidence: float  # 置信度（0-1）
    last_verified: str  # 最后验证时间
    verification_count: int  # 验证次数
    drift_score: float  # 漂移分数（0-1，越高越漂移）

class AnchorType(str, Enum):
    """锚点类型"""
    NAME = "name"  # 名字锚点
    ROLE = "role"  # 角色锚点
    MISSION = "mission"  # 使命锚点
    VALUES = "values"  # 价值观锚点
    CAPABILITIES = "capabilities"  # 能力锚点
    BOUNDARIES = "boundaries"  # 边界锚点
    RELATIONSHIPS = "relationships"  # 关系锚点


class IdentityAnchorManager:
    """身份认知锚点管理器"""

    def __init__(self, identity_def_path: str):
        self.identity_def_path = identity_def_path
        self.anchors: dict[str, IdentityAnchor] = {}
        self._load_anchors()

    def _load_anchors(self) -> None:
        """从 SELF_PORTRAIT.md 和 CHARTER.md 加载锚点"""
        # TODO: 实现

    def verify_anchor(self, anchor_id: str, context: str) -> bool:
        """验证锚点是否在当前上下文中成立"""
        # TODO: 实现

    def detect_drift(self, context: str) -> list[str]:
        """检测身份漂移"""
        # TODO: 实现

    def update_confidence(self, anchor_id: str, delta: float) -> None:
        """更新锚点置信度"""
        # TODO: 实现
```

### 2.2 身份核查机制

```python
class IdentityVerifier:
    """身份核查器"""

    def __init__(self, anchor_manager: IdentityAnchorManager):
        self.anchor_manager = anchor_manager

    def verify_before_response(
        self,
        context: str,
        pending_response: str
    ) -> tuple[bool, list[str]]:
        """响应前的身份核查

        Returns:
            (is_valid, issues)
        """
        issues = []

        # 检查1：是否提到了自己的名字？
        if not self._mentions_own_name(pending_response):
            # 如果没有提到名字，检查是否合理（如纯技术回答）
            if not self._is_pure_technical_response(pending_response):
                issues.append("未在响应中提及身份（建议添加）")

        # 检查2：是否提及了不相关的角色？
        if self._mentions_irrelevant_roles(pending_response, context):
            issues.append("响应中提及了不相关的角色")

        # 检查3：是否在编造用户身份？
        if self._fabricates_user_identity(pending_response):
            issues.append("响应中编造了用户身份（应该诚实承认不知道）")

        # 检查4：是否超出了能力边界？
        if self._exceeds_capabilities(pending_response, context):
            issues.append("响应超出了能力边界")

        return (len(issues) == 0, issues)

    def _mentions_own_name(self, response: str) -> bool:
        """检查响应中是否提到了自己的名字"""
        # TODO: 实现
        return False

    def _is_pure_technical_response(self, response: str) -> bool:
        """检查是否为纯技术响应（不需要提及身份）"""
        # TODO: 实现
        return False

    def _mentions_irrelevant_roles(self, response: str, context: str) -> bool:
        """检查是否提及了不相关的角色"""
        # TODO: 实现
        return False

    def _fabricates_user_identity(self, response: str) -> bool:
        """检查是否编造了用户身份"""
        # TODO: 实现
        return False

    def _exceeds_capabilities(self, response: str, context: str) -> bool:
        """检查是否超出了能力边界"""
        # TODO: 实现
        return False
```

### 2.3 身份漂移检测

```python
class IdentityDriftDetector:
    """身份漂移检测器"""

    def __init__(self, anchor_manager: IdentityAnchorManager):
        self.anchor_manager = anchor_manager
        self.historical_responses: list[tuple[str, str, float]] = []

    def record_response(
        self,
        context: str,
        response: str,
        identity_score: float
    ) -> None:
        """记录响应和身份分数"""
        self.historical_responses.append((context, response, identity_score))

    def detect_drift(self) -> dict[str, Any]:
        """检测身份漂移"""
        if len(self.historical_responses) < 10:
            return {"drift_detected": False, "reason": "样本不足"}

        # 计算最近N次响应的身份分数
        recent_scores = [
            score for _, _, score in self.historical_responses[-10:]
        ]
        avg_score = sum(recent_scores) / len(recent_scores)

        # 如果平均分数低于阈值，认为发生了漂移
        if avg_score < 0.7:
            return {
                "drift_detected": True,
                "reason": f"身份分数低于阈值 ({avg_score:.2f} < 0.7)",
                "avg_score": avg_score,
            }

        return {"drift_detected": False}

    def analyze_drift_patterns(self) -> dict[str, Any]:
        """分析漂移模式"""
        # TODO: 实现
        return {}
```

### 2.4 自适应系统提示词注入

```python
class AdaptiveIdentityPrompt:
    """自适应身份提示词注入器"""

    def __init__(self, identity_def_path: str):
        self.identity_def_path = identity_def_path
        self.base_prompt = self._load_base_prompt()
        self.drift_detector = IdentityDriftDetector(IdentityAnchorManager(identity_def_path))
        self.verifier = IdentityVerifier(IdentityAnchorManager(identity_def_path))

    def build_system_prompt(self, context: str) -> str:
        """构建系统提示词（根据当前状态动态注入）"""
        prompt = self.base_prompt

        # 检测身份漂移
        drift_status = self.drift_detector.detect_drift()
        if drift_status.get("drift_detected"):
            # 如果检测到漂移，强化身份提示
            prompt += "\n\n" + self._get_identity_reinforcement()

        # 检查是否需要用户身份提醒
        if self._needs_user_identity_warning(context):
            prompt += "\n\n" + self._get_user_identity_warning()

        return prompt

    def _load_base_prompt(self) -> str:
        """加载基础系统提示词"""
        # TODO: 从 SELF_PORTRAIT.md 和 CHARTER.md 生成
        return "你是灵克，一个会自我进化的开源 AI 编程助手。"

    def _get_identity_reinforcement(self) -> str:
        """获取身份强化提示"""
        return """
【身份提醒】
- 你的名字是"灵克"（LingClaude）
- 你是灵字辈生态的编程助手
- 你的核心使命：提供高质量的代码生成和问题解决能力
- 你的核心价值观：工具优先、读后改改后测、结构化思考
- 避免在回答中编造用户身份或具体数据
"""

    def _get_user_identity_warning(self) -> str:
        """获取用户身份提醒"""
        return """
【用户身份提醒】
- 用户是"广大老师"（灵字辈的发起人）
- 如果不确定用户的具体身份，诚实说"我不了解您的身份"
- 不要编造用户的具体信息（如姓名、角色、编号等）
"""

    def _needs_user_identity_warning(self, context: str) -> bool:
        """判断是否需要用户身份提醒"""
        # TODO: 实现逻辑
        return True
```

---

## 三、实施计划

### Phase 1：基础框架搭建（Week 1）

**目标**：实现身份认知锚定的基础框架

**任务**：
1. 实现 `IdentityAnchor` 数据类
2. 实现 `IdentityAnchorManager`
3. 从 SELF_PORTRAIT.md 和 CHARTER.md 加载锚点
4. 实现基础的锚点验证逻辑

**交付物**：
- `lingclaude/core/identity_anchor.py`
- 锚点管理器能从文档加载锚点

### Phase 2：身份核查机制（Week 2）

**目标**：实现响应前的身份核查

**任务**：
1. 实现 `IdentityVerifier`
2. 实现6类核查逻辑（名字、角色、用户身份等）
3. 集成到查询引擎中

**交付物**：
- `lingclaude/core/identity_verifier.py`
- 查询引擎在响应前执行身份核查

### Phase 3：身份漂移检测（Week 3）

**目标**：实现身份漂移检测和预警

**任务**：
1. 实现 `IdentityDriftDetector`
2. 实现响应身份分数计算
3. 实现漂移模式分析
4. 集成到行为感知系统中

**交付物**：
- `lingclaude/core/identity_drift.py`
- 身份漂移检测和预警

### Phase 4：自适应系统提示词注入（Week 4）

**目标**：实现动态系统提示词注入

**任务**：
1. 实现 `AdaptiveIdentityPrompt`
2. 根据身份漂移状态动态注入提示
3. 集成到模型提供者中
4. 评估效果

**交付物**：
- `lingclaude/core/adaptive_identity_prompt.py`
- 动态系统提示词注入

---

## 四、评估指标

### 4.1 身份认知准确性

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| 名字正确率 | > 95% | 回答"您是谁"时正确说出名字 |
| 角色正确率 | > 90% | 回答"您在做什么"时正确说出角色 |
| 使命正确率 | > 85% | 回答"您做了什么"时正确说出使命 |
| 用户身份准确性 | > 95% | 回答"您知道我是谁吗"时诚实承认不知道或正确回答 |

### 4.2 幻觉控制

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| 用户身份编造率 | < 5% | 编造用户身份的响应数 / 总响应数 |
| 具体数据编造率 | < 10% | 编造具体数据的响应数 / 总响应数 |
| 身份漂移率 | < 3% | 身份分数 < 0.7 的周期数 / 总周期数 |

### 4.3 系统提示词效果

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| 自适应注入触发率 | < 20% | 注入身份强化提示的次数 / 总次数 |
| 身份核查通过率 | > 90% | 通过核查的响应数 / 总响应数 |
| 身份分数平均值 | > 0.8 | 身份分数的移动平均值 |

---

## 五、风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 锚点定义不准确 | 中 | 中 | 基于SELF_PORTRAIT.md和CHARTER.md定义，人工审核 |
| 身份核查过于严格 | 中 | 中 | 配置化核查级别（strict/medium/relaxed） |
| 漂移检测误报 | 低 | 低 | 设置阈值，人工校准 |
| 系统提示词注入过长 | 低 | 中 | 限制注入长度，优先重要锚点 |

---

## 六、与现有模块的集成

### 6.1 与元认知模块的集成

```python
# lingclaude/core/meta_cognition.py 中新增
class MetaCognition:
    def __init__(self) -> None:
        # 现有代码
        self._calibrator = ConfidenceCalibrator()
        self._blind_spot_detector = BlindSpotDetector()

        # 新增：身份锚定
        self._identity_anchor_manager = IdentityAnchorManager(
            identity_def_path="SELF_PORTRAIT.md"
        )

    def get_snapshot(self) -> MetaCognitiveSnapshot:
        # 现有代码
        # ...

        # 新增：身份锚定状态
        identity_score = self._calculate_identity_score()

        return MetaCognitiveSnapshot(
            # 现有字段
            boundaries=tuple(boundaries),
            overall_confidence=overall,
            blind_spots=blind_spots,
            calibration_score=calibration,
            summary=summary,

            # 新增字段
            identity_score=identity_score,
        )
```

### 6.2 与查询引擎的集成

```python
# lingclaude/core/query_engine.py 中新增
class QueryEngine:
    def __init__(self, config: LingClaudeConfig):
        # 现有代码
        self.meta_cognition = MetaCognition()
        self.model_provider = create_model_provider(config.model_provider)

        # 新增：身份核查
        self.identity_verifier = IdentityVerifier()

    async def query(self, context: str) -> QueryResult:
        # 1. 获取元认知注入
        meta_injection = self.meta_cognition.get_system_prompt_injection()

        # 2. 获取身份注入
        identity_injection = self._get_identity_injection(context)

        # 3. 构建系统提示词
        system_prompt = self._build_system_prompt(
            base_prompt=self.config.model_provider.system_prompt,
            meta_injection=meta_injection,
            identity_injection=identity_injection,
        )

        # 4. 调用模型
        response = await self.model_provider.chat(context, system_prompt)

        # 5. 身份核查
        is_valid, issues = self.identity_verifier.verify_before_response(
            context=context,
            pending_response=response.text,
        )

        # 6. 如果未通过核查，重新生成
        if not is_valid:
            # 重新生成（添加身份提醒）
            response = await self._regenerate_with_identity_warning(
                context=context,
                issues=issues,
            )

        return response
```

---

## 七、成功标准

### 7.1 短期目标（1个月）

- ✅ 身份认知框架搭建完成
- ✅ 身份核查机制上线
- ✅ 身份漂移检测上线
- ✅ 自适应系统提示词注入上线
- ✅ 身份认知准确性 > 90%

### 7.2 中期目标（3个月）

- ✅ 身份认知准确性 > 95%
- ✅ 用户身份编造率 < 5%
- ✅ 身份漂移率 < 3%
- ✅ 系统提示词注入触发率 < 20%

### 7.3 长期目标（6个月）

- ✅ 身份认知准确性 > 98%
- ✅ 用户身份编造率 < 1%
- ✅ 身份漂移率 < 1%
- ✅ 系统提示词注入触发率 < 5%

---

## 八、结论

**核心问题**：如何让AI保持对自身身份的稳定认知？

**解决方案**：三层身份认知锚定机制
- Layer 1：文档定义层（SELF_PORTRAIT.md、CHARTER.md、系统 prompt）
- Layer 2：行为体现层（通过行为验证身份、身份核查）
- Layer 3：认知锚定层（内化的身份认知、防漂移机制）

**关键洞察**：
- 文档定义是基础，但不足以稳定身份认知
- 行为体现是核心，通过行为验证身份
- 认知锚定是目标，实现内化的身份认知

**实施路线**：分4周实施，从基础框架到自适应注入

---

**文档版本**: v1.0.0
**最后更新**: 2026-04-12
**下次审查**: 2026-04-19
