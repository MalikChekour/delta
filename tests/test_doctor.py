from __future__ import annotations

from hermes import doctor


def test_liste_blanche_renseignee(settings):
    check = doctor._whitelist(settings)
    assert check.level == doctor.OK


def test_liste_blanche_vide_est_bloquante(settings):
    vide = type(settings)(**{**settings.__dict__, "allowed_users": frozenset()})
    assert doctor._whitelist(vide).level == doctor.FAIL


def test_liste_blanche_vide_avec_appropriation(settings):
    ouvert = type(settings)(
        **{**settings.__dict__, "allowed_users": frozenset(), "claim_owner": True}
    )
    assert doctor._whitelist(ouvert).level == doctor.WARN


def test_proprietaire_enregistre_compte_comme_autorisation(settings):
    from hermes.memory import Store

    Store(settings.data_dir / "hermes.db").claim(5, "moi")
    vide = type(settings)(**{**settings.__dict__, "allowed_users": frozenset()})
    check = doctor._whitelist(vide)
    assert check.level == doctor.OK and "appropriation" in check.detail


def test_rendu_du_verdict():
    checks = [doctor.Check(doctor.OK, "A"), doctor.Check(doctor.FAIL, "B", "casse")]
    rendu = doctor.render(checks)
    assert "casse" in rendu and "1 probleme" in rendu


def test_rendu_sans_probleme():
    assert "Tout est en place" in doctor.render([doctor.Check(doctor.OK, "A")])


async def test_diagnostic_sans_aucune_cle(settings):
    muet = type(settings)(**{**settings.__dict__, "telegram_token": ""})
    checks = await doctor.run(muet)
    assert any(check.level == doctor.FAIL for check in checks)
