"""WQ-8 classifier narrowing — c97ad7f removed bare 'ausfüllen'."""

from universal_auto_applier.core.statuses import ClickableClassification
from universal_auto_applier.navigator.clickable_classifier import classify_clickable


class TestMsgExactCTA:
    def test_bewerbungsformular_ausfuellen_is_safe_apply(self) -> None:
        result = classify_clickable(text="Bewerbungsformular ausfüllen", tag="a")
        assert result.classification == ClickableClassification.SAFE_APPLY

    def test_bewerbungsformular_ausfuellen_with_newline_is_safe_apply(self) -> None:
        result = classify_clickable(text="Bewerbungsformular\nausfüllen", tag="a")
        assert result.classification == ClickableClassification.SAFE_APPLY

    def test_bewerbungsformular_ausfuellen_lowercase_is_safe_apply(self) -> None:
        result = classify_clickable(text="bewerbungsformular ausfüllen", tag="button")
        assert result.classification == ClickableClassification.SAFE_APPLY


class TestBareAusfuellenIsNotSafe:
    def test_profil_ausfuellen_is_unknown(self) -> None:
        result = classify_clickable(text="Profil ausfüllen", tag="button")
        assert result.classification == ClickableClassification.UNKNOWN

    def test_umfrage_ausfuellen_is_unknown(self) -> None:
        result = classify_clickable(text="Umfrage ausfüllen", tag="a")
        assert result.classification == ClickableClassification.UNKNOWN

    def test_formular_ausfuellen_is_unknown(self) -> None:
        result = classify_clickable(text="Formular ausfüllen", tag="button")
        assert result.classification == ClickableClassification.UNKNOWN

    def test_bewerbungsformular_alone_is_unknown(self) -> None:
        # Bare "Bewerbungsformular" without ausfüllen should not be safe (exact phrase required)
        result = classify_clickable(text="Bewerbungsformular", tag="a")
        assert result.classification == ClickableClassification.UNKNOWN


class TestDangerousStillWins:
    def test_bewerbungsformular_absenden_is_dangerous(self) -> None:
        result = classify_clickable(text="Bewerbungsformular absenden", tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT

    def test_absenden_und_bewerben_is_dangerous(self) -> None:
        result = classify_clickable(text="Absenden und bewerben", tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT

    def test_bewerbung_absenden_is_dangerous(self) -> None:
        result = classify_clickable(text="Bewerbung absenden", tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT

    def test_absenden_alone_is_dangerous(self) -> None:
        result = classify_clickable(text="Absenden", tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT
