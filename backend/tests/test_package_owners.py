"""Each table's model, schema and helper lives in the package that owns it, so imports follow
the feature they are about (fix plan Β1). These checks keep a moved class from moving back."""

import pytest

from app.api import cursors
from app.articles import models as article_models
from app.articles import schemas as article_schemas
from app.entities import models as entity_models

ENTITY_TABLES = [
    "Entity",
    "EntityNameClaim",
    "EntityDistinct",
    "EntityAuthorityChange",
    "EntityAuthorityRun",
    "EntityRelation",
]
ARTICLE_TABLES = ["Article", "ArticleContent", "ArticleProcessingJob"]


@pytest.mark.parametrize("name", ENTITY_TABLES)
def test_the_entities_package_owns_the_entity_tables(name: str) -> None:
    assert getattr(entity_models, name).__module__ == "app.entities.models"


@pytest.mark.parametrize("name", ARTICLE_TABLES)
def test_the_articles_package_owns_the_article_tables(name: str) -> None:
    assert getattr(article_models, name).__module__ == "app.articles.models"


def test_the_article_response_lives_with_the_articles() -> None:
    assert article_schemas.ArticleResponse.__module__ == "app.articles.schemas"


@pytest.mark.parametrize("name", ["read_cursor", "sign_cursor"])
def test_the_signed_cursors_are_shared_api_helpers(name: str) -> None:
    assert getattr(cursors, name).__module__ == "app.api.cursors"
