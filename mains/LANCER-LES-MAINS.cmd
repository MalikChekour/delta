@echo off
REM Lance les MAINS de l'agent (ecran, souris, clavier) dans CETTE session Windows.
REM
REM A lancer depuis votre session Bureau a distance, pas en tache planifiee :
REM l'agent tourne en session 0 et n'a aucun acces au bureau. C'est ce programme,
REM lance par vous, qui lui prete des yeux et des mains.
REM
REM Fermez cette fenetre pour retirer les mains. Sans elle, l'agent n'a ni ecran,
REM ni souris, ni clavier — quoi qu'il demande.

title Les mains de l'agent — fermez pour les retirer
cd /d "%~dp0.."
echo.
echo   Demarrage des mains de l'agent...
echo   Fermez cette fenetre pour les lui retirer.
echo.
"%~dp0..\venv\Scripts\python.exe" "%~dp0mains.py"
echo.
echo   Les mains sont retirees. L'agent n'a plus acces au bureau.
pause
