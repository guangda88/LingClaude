# L7 Cognitive Layer test suite
import pytest
from lingclaude.core.l7_cognitive import (
    L7Cognitive, MemoryTier, OKFType, MessageCategory,
    CognitiveMemory, DocIndex, GlossaryTerm, GraphEdge,
    CognitiveStore, MessageClassifier, SessionHooks,
    importance_to_tier,
)


@pytest.fixture
def cog(tmp_path):
    db = tmp_path / "test_l7c.db"
    c = L7Cognitive(db_path=str(db))
    yield c
    c.close()


class TestImportanceToTier:
    def test_always(self):
        assert importance_to_tier(9) == MemoryTier.ALWAYS
        assert importance_to_tier(8) == MemoryTier.ALWAYS

    def test_ondemand(self):
        assert importance_to_tier(7) == MemoryTier.ONDEMAND
        assert importance_to_tier(3) == MemoryTier.ONDEMAND

    def test_triggered(self):
        assert importance_to_tier(2) == MemoryTier.TRIGGERED
        assert importance_to_tier(1) == MemoryTier.TRIGGERED


class TestCognitiveMemory:
    def test_auto_tier(self):
        mem = CognitiveMemory(key="test", value="val", importance=9)
        assert mem.tier == MemoryTier.ALWAYS

    def test_auto_id(self):
        mem = CognitiveMemory(key="test", value="val")
        assert len(mem.id) == 12

    def test_auto_timestamp(self):
        mem = CognitiveMemory(key="test", value="val")
        assert mem.created_at > 0


class TestMessageClassifier:
    def test_decision(self):
        c = MessageClassifier()
        assert c.classify("我们决定用 DeepSeek") == MessageCategory.DECISION

    def test_incident(self):
        c = MessageClassifier()
        assert c.classify("proxy3 崩溃了") == MessageCategory.INCIDENT

    def test_achievement(self):
        c = MessageClassifier()
        assert c.classify("已部署到生产环境") == MessageCategory.ACHIEVEMENT

    def test_general(self):
        c = MessageClassifier()
        assert c.classify("今天天气不错") == MessageCategory.GENERAL

    def test_extract_profile_gpu(self):
        c = MessageClassifier()
        mems = c.extract_profile("我的显卡是 RTX 4090")
        assert len(mems) >= 1
        assert any("gpu" in m.key for m in mems)

    def test_extract_profile_model(self):
        c = MessageClassifier()
        mems = c.extract_profile("我使用 DeepSeek 模型")
        assert len(mems) >= 1
        assert any("model" in m.key for m in mems)


class TestCognitiveStore:
    def test_put_get_memory(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        mem = CognitiveMemory(key="k1", value="v1", importance=8)
        mid = store.put_memory(mem)
        got = store.get_memory(mid)
        assert got is not None
        assert got.key == "k1"
        store.close()

    def test_always_context(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        store.put_memory(CognitiveMemory(key="north", value="star", importance=9))
        store.put_memory(CognitiveMemory(key="low", value="temp", importance=2))
        always = store.get_always_context()
        assert len(always) == 1
        assert always[0].key == "north"
        store.close()

    def test_ondemand_context(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        store.put_memory(CognitiveMemory(key="deepseek", value="model", importance=5))
        results = store.get_ondemand_context("deepseek")
        assert len(results) == 1
        store.close()

    def test_search_by_type(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        store.put_memory(CognitiveMemory(
            key="dec1", value="use deepseek", importance=7, okf_type=OKFType.DECISION
        ))
        store.put_memory(CognitiveMemory(
            key="hw1", value="RTX 4090", importance=6, okf_type=OKFType.HARDWARE
        ))
        results = store.search("deepseek", okf_type=OKFType.DECISION)
        assert len(results) == 1
        assert results[0].okf_type == OKFType.DECISION
        store.close()


class TestDocIndex:
    def test_put_search_doc(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        doc = DocIndex(
            path="/some/path.py", title="L7 Memory",
            summary="L7 统一记忆层", okf_type=OKFType.TOOL, project="lingminopt",
        )
        store.put_doc(doc)
        results = store.search_docs("L7")
        assert len(results) == 1
        assert results[0].title == "L7 Memory"
        store.close()


class TestGlossary:
    def test_put_lookup(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        term = GlossaryTerm(
            term="灵元", definition="灵族推理栈",
            aliases=["lingyuan"], source_thread="t1",
        )
        store.put_glossary(term)
        got = store.lookup_glossary("灵元")
        assert got is not None
        assert got.definition == "灵族推理栈"
        store.close()

    def test_lookup_alias(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        store.put_glossary(GlossaryTerm(
            term="灵元", definition="灵族推理栈", aliases=["lingyuan"],
        ))
        got = store.lookup_glossary("lingyuan")
        assert got is not None
        store.close()


class TestGraphEdge:
    def test_put_get_neighbors(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        store.put_memory(CognitiveMemory(key="a", value="1", importance=5))
        store.put_memory(CognitiveMemory(key="b", value="2", importance=5))
        store.put_edge(GraphEdge(source_id="x", target_id="y", relation="depends_on"))
        neighbors = store.get_neighbors("x", depth=1)
        assert len(neighbors["edges"]) >= 1
        store.close()


class TestSessionHooks:
    def test_session_lifecycle(self, tmp_path):
        store = CognitiveStore(db_path=str(tmp_path / "test.db"))
        store.put_memory(CognitiveMemory(
            key="north_star", value="goal", importance=9, okf_type=OKFType.DECISION,
        ))
        hooks = SessionHooks(store)
        start = hooks.on_session_start("s1", "lingclaude")
        assert start["always_count"] == 1
        assert "north_star" in start["context"]

        msg = hooks.on_user_prompt("s1", "我决定用 DeepSeek")
        assert msg["category"] == "decision"

        stop = hooks.on_session_stop("s1", "我决定用 DeepSeek", "ok")
        assert stop["extracted_count"] >= 1
        store.close()


class TestL7CognitiveTopLevel:
    def test_remember_recall(self, cog):
        cog.remember("test_key", "test_value", importance=7, okf_type=OKFType.DECISION)
        results = cog.recall("test")
        assert len(results) == 1
        assert results[0]["key"] == "test_key"

    def test_remember_recall_compact(self, cog):
        cog.remember("k", "v", importance=7, okf_type=OKFType.CONCEPT)
        text = cog.recall_compact("k")
        assert "k: v" in text

    def test_index_find_docs(self, cog):
        cog.index_doc("/path/to/file.py", "Test Doc", "summary here",
                      okf_type=OKFType.TOOL, project="test")
        docs = cog.find_docs("Test")
        assert len(docs) == 1
        assert docs[0]["title"] == "Test Doc"

    def test_define_lookup_term(self, cog):
        cog.define_term("测试术语", "测试定义", aliases=["test_term"])
        result = cog.lookup_term("测试术语")
        assert "测试定义" in result

    def test_link_graph(self, cog):
        mid = cog.remember("node1", "val1", importance=5)
        did = cog.index_doc("/p", "Doc", "sum")
        cog.link(mid, did, relation="depends_on")
        g = cog.graph(mid, depth=1)
        assert len(g["edges"]) >= 1

    def test_full_session(self, cog):
        cog.remember("north", "star goal", importance=9, okf_type=OKFType.DECISION)
        start = cog.start_session("s-full", "lingclaude")
        assert start["always_count"] == 1

        msg = cog.on_message("s-full", "我决定用 RTX 4090 跑 DeepSeek")
        assert msg["category"] == "decision"
        assert msg["profile_extracted"] >= 1

        cog.on_tool_use("s-full", "edit", "edited file.py")
        cog.on_pre_compact("s-full", [{"role": "user", "content": "test"}])

        stop = cog.stop_session("s-full", "决定用 DeepSeek", "ok")
        assert stop["extracted_count"] >= 1

    def test_stats(self, cog):
        s = cog.stats()
        assert "memories" in s
        assert "docs" in s
        assert "glossary" in s
        assert "edges" in s
        assert "l7_engine_connected" in s
