from f3d2solidcraft_app.texts import TEXTS, language, t


def test_both_languages_have_the_same_keys():
    assert set(TEXTS["es"]) == set(TEXTS["en"])


def test_the_language_follows_the_system_locale():
    assert language("es_CL.UTF-8") == "es"
    assert language("es") == "es"
    assert language("en_US") == "en"
    assert language("de_DE") == "en"
    assert language("") == "en"


def test_values_are_filled_in():
    assert t("progress", "es", index=16, total=26, name="Extrude 9") == "Operación 16 de 26 · Extrude 9"
    assert t("progress", "en", index=16, total=26, name="Extrude 9") == "Feature 16 of 26 · Extrude 9"


def test_spanish_is_neutral():
    # Product copy is neutral Spanish: no voseo and no Chilean slang (project convention).
    banned = ["podés", "tenés", "querés", "soltá", "elegí", "acá", " po ", "cachái"]
    for text in TEXTS["es"].values():
        assert not any(word in f" {text} " for word in banned), text
