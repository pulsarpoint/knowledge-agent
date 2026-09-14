import pytest

from rag import config


def test_bare_folder_wraps_single_collection(tmp_path):
    docs = tmp_path / "my-docs"
    docs.mkdir()
    cfg = config.load_config(docs)
    assert [c.name for c in cfg.collections] == ["my-docs"]
    assert cfg.collections[0].path == docs.resolve()


def test_toml_collections_validated(tmp_path):
    (tmp_path / "d").mkdir()
    good = tmp_path / "ok.toml"
    good.write_text(
        f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\n',
        encoding="utf-8")
    cfg = config.load_config(good)
    assert cfg.collections[0].name == "a"
    for body in [
        "collections = 5\n",
        "[[collections]]\nname = 1\npath = \"x\"\n",
        f'[[collections]]\nname = "a"\npath = "{tmp_path / "missing"}"\n',
        (f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\n'
         f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\n'),
    ]:
        bad = tmp_path / "bad.toml"
        bad.write_text(body, encoding="utf-8")
        with pytest.raises(config.ConfigError):
            config.load_config(bad)


def test_resolve_embed_env_over_file_and_completeness():
    resolved = config.resolve_embed(
        {"embed_base_url": "http://file/v1", "embed_model": "m"},
        env={"RAG_EMBED_BASE_URL": "http://env/v1"})
    assert resolved.base_url == "http://env/v1"
    assert resolved.embed_model == "m"
    assert config.resolve_embed({"embed_base_url": "http://x/v1"},
                                env={}) is None
    assert config.resolve_embed(None, env={}) is None


def test_collection_kind_roundtrips(tmp_path):
    (tmp_path / "d").mkdir()
    cfg_toml = tmp_path / "ok.toml"
    cfg_toml.write_text(
        f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\nkind = "code"\n',
        encoding="utf-8")
    cfg = config.load_config(cfg_toml)
    assert cfg.collections[0].kind == "code"


def test_collection_kind_defaults_to_docs(tmp_path):
    (tmp_path / "d").mkdir()
    cfg_toml = tmp_path / "ok.toml"
    cfg_toml.write_text(
        f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\n',
        encoding="utf-8")
    cfg = config.load_config(cfg_toml)
    assert cfg.collections[0].kind == "docs"


def test_bare_folder_defaults_kind_to_docs(tmp_path):
    docs = tmp_path / "my-docs"
    docs.mkdir()
    cfg = config.load_config(docs)
    assert cfg.collections[0].kind == "docs"


def test_collection_kind_invalid_raises_config_error(tmp_path):
    (tmp_path / "d").mkdir()
    cfg_toml = tmp_path / "bad.toml"
    cfg_toml.write_text(
        f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\nkind = "wiki"\n',
        encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_config(cfg_toml)
    assert "collection 'a': kind must be one of docs, code, memory" in str(exc_info.value)


# ---- db_dir (relocatable databases, backoffice-managed collections) ------

def test_toml_collection_db_dir_defaults_to_none(tmp_path):
    (tmp_path / "d").mkdir()
    cfg_toml = tmp_path / "ok.toml"
    cfg_toml.write_text(
        f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\n',
        encoding="utf-8")
    cfg = config.load_config(cfg_toml)
    assert cfg.collections[0].db_dir is None


def test_bare_folder_db_dir_defaults_to_none(tmp_path):
    docs = tmp_path / "my-docs"
    docs.mkdir()
    cfg = config.load_config(docs)
    assert cfg.collections[0].db_dir is None


def test_toml_collection_db_dir_parses_and_resolves(tmp_path):
    (tmp_path / "d").mkdir()
    db_dir = tmp_path / "elsewhere" / "a-db"
    cfg_toml = tmp_path / "ok.toml"
    cfg_toml.write_text(
        f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\n'
        f'db_dir = "{db_dir}"\n',
        encoding="utf-8")
    cfg = config.load_config(cfg_toml)
    assert cfg.collections[0].db_dir == db_dir.resolve()


def test_toml_collection_db_dir_non_string_raises_config_error(tmp_path):
    (tmp_path / "d").mkdir()
    cfg_toml = tmp_path / "bad.toml"
    cfg_toml.write_text(
        f'[[collections]]\nname = "a"\npath = "{tmp_path / "d"}"\n'
        'db_dir = 5\n',
        encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc_info:
        config.load_config(cfg_toml)
    assert "db_dir must be a string" in str(exc_info.value)
