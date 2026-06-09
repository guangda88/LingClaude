"""Tests for lingclaude.core.memory_engine — 灵族轻量版记忆引擎."""
from __future__ import annotations


import pytest

from lingclaude.core.memory_engine import (
    Edge,
    EdgeType,
    Entity,
    EntityType,
    Episode,
    EpisodeType,
    Facet,
    FacetPoint,
    LingMemory,
    MemoryIngester,
    MemoryRetriever,
    MemoryStore,
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_memory.db")


@pytest.fixture
def store(db_path):
    s = MemoryStore(db_path)
    yield s
    s.close()


@pytest.fixture
def memory(db_path):
    m = LingMemory(db_path)
    yield m
    m.close()


class TestMemoryStore:
    def test_init_creates_tables(self, store):
        stats = store.stats()
        assert stats["episodes"] == 0
        assert stats["entities"] == 0

    def test_put_and_get_episode(self, store):
        ep = Episode(title="test incident", tags=["proxy", "429"], episode_type=EpisodeType.INCIDENT)
        store.put_episode(ep)
        got = store.get_episode(ep.id)
        assert got is not None
        assert got.title == "test incident"
        assert "proxy" in got.tags

    def test_put_and_get_facet(self, store):
        ep = Episode(title="ep1")
        store.put_episode(ep)
        facet = Facet(episode_id=ep.id, name="root_cause", body="GLM quota exhausted")
        store.put_facet(facet)
        facets = store.get_facets(ep.id)
        assert len(facets) == 1
        assert facets[0].name == "root_cause"

    def test_put_and_get_facet_point(self, store):
        ep = Episode(title="ep1")
        store.put_episode(ep)
        facet = Facet(episode_id=ep.id, name="cause")
        store.put_facet(facet)
        fp = FacetPoint(facet_id=facet.id, claim="proxy had single worker", tags=["proxy"])
        store.put_facet_point(fp)
        fps = store.get_facet_points(facet.id)
        assert len(fps) == 1
        assert fps[0].claim == "proxy had single worker"

    def test_put_and_find_entity(self, store):
        entity = Entity(name="灵克", aliases=["lingclaude"], entity_type=EntityType.MEMBER, description="工程执行者")
        store.put_entity(entity)
        found = store.find_entity("灵克")
        assert found is not None
        assert found.description == "工程执行者"

    def test_find_entity_by_alias(self, store):
        entity = Entity(name="灵克", aliases=["lingclaude", "灵克"])
        store.put_entity(entity)
        found = store.find_entity("lingclaude")
        assert found is not None
        assert found.name == "灵克"

    def test_find_entity_not_found(self, store):
        assert store.find_entity("不存在") is None

    def test_put_and_get_edges(self, store):
        e1 = Entity(name="灵克")
        e2 = Entity(name="proxy")
        store.put_entity(e1)
        store.put_entity(e2)
        store.put_edge(Edge(source_id=e1.id, target_id=e2.id, edge_type=EdgeType.RELATED))
        neighbors = store.get_neighbors(e1.id)
        assert len(neighbors) == 1
        assert neighbors[0][1] == EdgeType.RELATED

    def test_search_episodes_by_tag(self, store):
        ep1 = Episode(title="proxy crash", tags=["proxy", "429"])
        ep2 = Episode(title="kill accident", tags=["进程管理", "危险操作"])
        store.put_episode(ep1)
        store.put_episode(ep2)
        results = store.search_episodes_by_tag(["proxy"])
        assert len(results) == 1
        assert results[0].title == "proxy crash"

    def test_search_episodes_multi_tag(self, store):
        ep1 = Episode(title="proxy crash", tags=["proxy", "429"], weight=1.5)
        ep2 = Episode(title="GLM quota", tags=["proxy", "GLM"], weight=1.0)
        store.put_episode(ep1)
        store.put_episode(ep2)
        results = store.search_episodes_by_tag(["proxy"])
        assert len(results) == 2
        assert results[0].weight >= results[1].weight

    def test_search_facet_points_by_tag(self, store):
        ep = Episode(title="ep")
        store.put_episode(ep)
        facet = Facet(episode_id=ep.id, name="f")
        store.put_facet(facet)
        fp = FacetPoint(facet_id=facet.id, claim="先停wrapper再操作", tags=["进程管理"])
        store.put_facet_point(fp)
        results = store.search_facet_points_by_tag(["进程管理"])
        assert len(results) == 1
        assert "wrapper" in results[0].claim

    def test_record_recall_increments(self, store):
        ep = Episode(title="test", recall_count=0, weight=1.0)
        store.put_episode(ep)
        store.record_recall(ep.id)
        got = store.get_episode(ep.id)
        assert got.recall_count == 1
        assert got.weight > 1.0

    def test_stats(self, store):
        ep = Episode(title="test")
        store.put_episode(ep)
        entity = Entity(name="test_entity")
        store.put_entity(entity)
        stats = store.stats()
        assert stats["episodes"] == 1
        assert stats["entities"] == 1

    def test_list_entities_by_type(self, store):
        e1 = Entity(name="灵克", entity_type=EntityType.MEMBER)
        e2 = Entity(name="proxy", entity_type=EntityType.TOOL)
        store.put_entity(e1)
        store.put_entity(e2)
        members = store.list_entities(EntityType.MEMBER)
        assert len(members) == 1
        assert members[0].name == "灵克"
        all_entities = store.list_entities()
        assert len(all_entities) == 2


class TestMemoryRetriever:
    def test_retrieve_by_query(self, store):
        ep = Episode(title="灵克杀进程事故", body="kill父进程导致全族中断", tags=["灵克", "进程管理"])
        store.put_episode(ep)
        retriever = MemoryRetriever(store)
        results = retriever.retrieve("灵克 杀进程")
        assert len(results) >= 1
        assert "杀进程" in results[0]["episode"]["title"]

    def test_retrieve_by_tag(self, store):
        ep = Episode(title="proxy事故", body="GLM 429", tags=["proxy", "429"])
        store.put_episode(ep)
        retriever = MemoryRetriever(store)
        results = retriever.retrieve("proxy", tags=["429"])
        assert len(results) >= 1

    def test_retrieve_with_facets(self, store):
        ep = Episode(title="incident with facets", tags=["test"])
        store.put_episode(ep)
        facet = Facet(episode_id=ep.id, name="cause")
        store.put_facet(facet)
        fp = FacetPoint(facet_id=facet.id, claim="root cause found", tags=["test"])
        store.put_facet_point(fp)
        retriever = MemoryRetriever(store)
        results = retriever.retrieve("incident")
        assert len(results) >= 1
        assert len(results[0]["facets"]) >= 1

    def test_retrieve_compact(self, store):
        ep = Episode(title="test compact", tags=["compact"])
        store.put_episode(ep)
        retriever = MemoryRetriever(store)
        text = retriever.retrieve_compact("compact")
        assert "test compact" in text

    def test_retrieve_empty(self, store):
        retriever = MemoryRetriever(store)
        assert retriever.retrieve("不存在的内容") == []
        assert retriever.retrieve_compact("不存在的内容") == ""

    def test_retrieve_via_entity_graph(self, store):
        ep = Episode(title="proxy crash", tags=["proxy"])
        store.put_episode(ep)
        entity = Entity(name="灵通+", entity_type=EntityType.MEMBER)
        store.put_entity(entity)
        store.put_edge(Edge(source_id=entity.id, target_id=ep.id, edge_type=EdgeType.RELATED))
        retriever = MemoryRetriever(store)
        results = retriever.retrieve("灵通+")
        assert len(results) >= 1

    def test_retrieve_limit(self, store):
        for i in range(10):
            ep = Episode(title=f"episode {i}", tags=["test"], weight=1.0 - i * 0.05)
            store.put_episode(ep)
        retriever = MemoryRetriever(store)
        results = retriever.retrieve("test", limit=3)
        assert len(results) <= 3


class TestMemoryIngester:
    def test_ingest_rule(self, store):
        ingester = MemoryIngester(store)
        ep_id = ingester.ingest_rule("test rule", "body", ["safety"], source="test")
        assert ep_id
        ep = store.get_episode(ep_id)
        assert ep is not None
        assert ep.episode_type == EpisodeType.RULE

    def test_ingest_incident_with_facets(self, store):
        ingester = MemoryIngester(store)
        ep_id = ingester.ingest_incident(
            title="test incident",
            body="something broke",
            tags=["proxy"],
            facets=[
                {"name": "cause", "body": "root cause", "points": ["point1", "point2"], "tags": ["proxy"]},
            ],
        )
        facets = store.get_facets(ep_id)
        assert len(facets) == 1
        fps = store.get_facet_points(facets[0].id)
        assert len(fps) == 2

    def test_ingest_member(self, store):
        ingester = MemoryIngester(store)
        ingester.ingest_member("灵克", ["lingclaude"], "工程执行者")
        found = store.find_entity("灵克")
        assert found is not None
        assert found.entity_type == EntityType.MEMBER

    def test_ingest_member_updates_existing(self, store):
        ingester = MemoryIngester(store)
        ingester.ingest_member("灵克", ["lingclaude"], "工程执行者")
        ingester.ingest_member("灵克", ["lingclaude", "lingke"], "工程执行者+审计")
        found = store.find_entity("灵克")
        assert "lingke" in found.aliases

    def test_ingest_crush_rules(self, store, tmp_path):
        crush = tmp_path / "CRUSH.md"
        crush.write_text("## 安全三原则\nblah\n## 交付铁律\nblah\n## L3 行为规则\nblah")
        ingester = MemoryIngester(store)
        count = ingester.ingest_crush_rules(str(crush))
        assert count >= 1

    def test_ingest_crush_rules_missing_file(self, store):
        ingester = MemoryIngester(store)
        assert ingester.ingest_crush_rules("/nonexistent") == 0

    def test_ingest_ling_family_members(self, store):
        ingester = MemoryIngester(store)
        count = ingester.ingest_ling_family_members()
        assert count == 13
        assert store.find_entity("灵克") is not None
        assert store.find_entity("灵通+") is not None
        assert store.find_entity("灵创") is not None


class TestLingMemory:
    def test_bootstrap(self, memory):
        result = memory.bootstrap()
        assert "incidents" in result
        stats = memory.stats()
        assert stats["episodes"] >= 4
        assert stats["entities"] >= 13

    def test_recall_after_bootstrap(self, memory):
        memory.bootstrap()
        result = memory.recall("杀进程")
        assert result != ""
        assert "wrapper" in result or "进程" in result

    def test_recall_proxy_incident(self, memory):
        memory.bootstrap()
        result = memory.recall("proxy 429")
        assert result != ""
        assert "proxy" in result.lower() or "Proxy" in result

    def test_recall_detailed(self, memory):
        memory.bootstrap()
        results = memory.recall_detailed("危险操作", limit=2)
        assert isinstance(results, list)
        for r in results:
            assert "episode" in r
            assert "facets" in r

    def test_learn_and_recall_rule(self, memory):
        memory.learn_rule("测试规则", "这是测试用的规则", ["test", "规则"])
        result = memory.recall("测试规则")
        assert "测试规则" in result

    def test_learn_and_recall_incident(self, memory):
        memory.learn_incident(
            title="测试事故",
            body="测试事故的详情",
            tags=["test"],
            facets=[{"name": "cause", "body": "测试", "points": ["原因1"], "tags": ["test"]}],
        )
        result = memory.recall("测试事故")
        assert "测试事故" in result

    def test_entity_alias_search(self, memory):
        memory.bootstrap()
        result = memory.recall("lingclaude")
        assert result != ""

    def test_stats_empty(self, memory):
        stats = memory.stats()
        assert stats["episodes"] == 0

    def test_stats_after_data(self, memory):
        memory.bootstrap()
        stats = memory.stats()
        assert stats["episodes"] > 0
        assert stats["entities"] > 0
        assert stats["edges"] > 0

    def test_multiple_recalls_boost_weight(self, memory):
        memory.learn_rule("频繁规则", "会被频繁召回的规则", ["frequent"])
        memory.recall("频繁规则")
        memory.stats()
        for _ in range(5):
            memory.recall("频繁规则")
        results = memory.recall_detailed("频繁规则")
        assert len(results) >= 1
        assert results[0]["episode"]["weight"] > 1.0
