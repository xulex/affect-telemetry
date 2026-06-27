"""
session_controller.py
=====================

Step 7a: Pre-flight UI and session orchestration for Phase 1 sessions.

Wraps start_session.sh and survey_prompter.py in a participant-facing
Tkinter flow. Captures pre-session metadata, demographics (first session
only), and baseline state. Spawns the session streams and the survey
prompter, monitors them through the 26-minute session, and transitions
to a completion screen when finished.

Step 7b will add the post-session debrief and log consolidation steps.

SCREENS
-------
  1. Operator splash         Participant ID, language, consent confirmation
  2. Demographics            Age, gender, native language, experience
                              (first session per participant only)
  3. Baseline questionnaire  Sleep, caffeine, mood, energy, discomfort
                              (every session, filled by participant)
  4. Equipment check         Operator checklist before session launch
  5. Session running         Countdown, subprocess status, abort button
  6. Session complete        Summary, path to session dir, exit

OUTPUTS
-------
  ~/thesis-phase1/participants/<participant_id>/demographics.json
      Written on first session only.

  ~/thesis-phase1/sessions/<participant_id>_<YYYYMMDDTHHMMSSZ>/
      session_metadata.json   Pre-session data: language, consent, baseline,
                              demographics (copied in), equipment checklist,
                              session timing, subprocess exit codes.
      (plus all streams written by start_session.sh and survey_prompter.py)

START_SESSION.SH MODIFICATION REQUIRED
---------------------------------------
Add this line near the top of start_session.sh (after argument parsing,
before the first stream is launched):

    SESSION_DIR="${SESSION_DIR:-$HOME/thesis-phase1/sessions/${2}_$(date -u +%Y%m%dT%H%M%SZ)}"

If SESSION_DIR is set in the environment (by this controller), it will be
used. Otherwise it falls back to the existing naming convention. This is a
non-breaking change: running start_session.sh directly from the command
line still works exactly as before.

USAGE
-----
    python session_controller.py
    python session_controller.py --duration 1560 --language en

For testing without spawning the actual streams:
    python session_controller.py --dry-run
"""

import os
import sys
import json
import time
import random
import shutil
import signal
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import tkinter as tk


# =============================================================================
# Paths and defaults
# =============================================================================

THESIS_DIR        = Path(os.environ.get("THESIS_DIR", Path(__file__).resolve().parent))
SESSIONS_DIR      = THESIS_DIR / 'sessions'
PARTICIPANTS_DIR  = THESIS_DIR / 'participants'
START_SESSION_SH  = THESIS_DIR / 'start_session.sh'
TASK_CHECKLIST    = THESIS_DIR / 'task_checklist.py'

DEFAULT_DURATION_SEC   = 1560      # 26 min
DEFAULT_PROMPT_TIMES   = '4,12,19'
DEFAULT_JITTER_SEC     = 45
SUBPROCESS_POLL_MS     = 500       # how often to tick the running screen
CLEANUP_GRACE_SEC      = 60        # max wait for start_session.sh cleanup trap.
                                   # Must exceed start_session.sh's own cleanup:
                                   # ~3s light-stream stop + up to 30s OBS finalize
                                   # + osquery slice. 60s leaves comfortable margin.


# =============================================================================
# Visual constants (shared style with survey_prompter.py)
# =============================================================================

COLOR_BG_WINDOW      = '#FFFFFF'
COLOR_BG_BUTTON      = '#F2F2F2'
COLOR_FG_BUTTON      = '#333333'
COLOR_BORDER_BUTTON  = '#CCCCCC'
COLOR_BG_SELECTED    = '#4A7AFF'
COLOR_FG_SELECTED    = '#FFFFFF'
COLOR_BG_PRIMARY_OFF = '#E5E5E5'
COLOR_FG_PRIMARY_OFF = '#999999'
COLOR_BG_PRIMARY_ON  = '#22C55E'
COLOR_BG_PRIMARY_HOT = '#16A34A'
COLOR_FG_PRIMARY_ON  = '#FFFFFF'
COLOR_BG_DANGER      = '#DC2626'
COLOR_BG_DANGER_HOT  = '#B91C1C'
COLOR_FG_DANGER      = '#FFFFFF'
COLOR_TEXT_TITLE     = '#222222'
COLOR_TEXT_BODY      = '#333333'
COLOR_TEXT_MUTED     = '#777777'
COLOR_BG_PROGRESS    = '#E5E7EB'
COLOR_FG_PROGRESS    = '#22C55E'

FLAG_FONT_SELECTED   = 22
FLAG_FONT_UNSELECTED = 14
FLAGS                = {'en': '🇬🇧', 'pt': '🇵🇹'}
SUPPORTED_LANGUAGES  = ('en', 'pt')


# =============================================================================
# Translations
# =============================================================================

STRINGS = {
    'en': {
        # Window
        'window_title': 'Phase 1 Session Controller',
        # Common
        'back':         'Back',
        'continue':     'Continue',
        'cancel':       'Cancel',
        # Splash
        'splash_title': 'Session Setup',
        'splash_subtitle': 'Operator: please fill in before handing the laptop to the participant.',
        'participant_name': 'Participant name',
        'participant_name_hint': '(for operator records only; mapped to a pseudonymous study ID)',
        'id_preview_new': 'Study ID:  {pid}  (new participant)',
        'id_preview_existing': 'Study ID:  {pid}  (existing participant, demographics will be reused)',
        'id_preview_empty': 'Study ID:  —  (enter a name above)',
        'language_label': 'Survey language for this session',
        # Digital consent (participant-facing, replaces paper)
        'consent_title': 'Informed Consent',
        'consent_subtitle': 'Please read carefully. Type your full name and check both boxes at the bottom to provide consent.',
        'consent_text': (
            'STUDY\n'
            'Phase 1 Calibration Study — Behavioral Telemetry as an Affective Instrument\n'
            'Master\u2019s thesis research, Berlin School of Creative Leadership / Steinbeis University Berlin.\n\n'
            'PURPOSE\n'
            'This study examines whether behavioral telemetry from typical knowledge-work software (typing '
            'rate, mouse activity, application focus, system events) can be combined with physiological '
            'signals (heart rate) and facial expression analysis to detect shifts in attention, focus, '
            'and emotional state during work.\n\n'
            'WHAT YOU WILL DO\n'
            'You will perform a 26-minute knowledge-work session involving reading, data analysis in '
            'Microsoft Excel, and synthesis writing. You will wear a Polar H10 chest strap that measures '
            'your heart rate. You will be asked three brief check-in questions at approximately minutes '
            '4, 12, and 19. Near the end of the session, you will be asked four brief demographic '
            'questions (first session only).\n\n'
            'DATA COLLECTED\n'
            '• Heart rate and heart rate variability from the chest strap.\n'
            '• Keystroke timing only — the rhythm of typing, NOT the content of what you type.\n'
            '• Mouse movement and click locations.\n'
            '• Which application is in focus at any given moment.\n'
            '• System activity events (process launches, file events, network events).\n'
            '• Video recording of your face for facial expression analysis (action units, head pose).\n'
            '• Your Likert responses to the in-session check-ins.\n'
            '• Your demographic information (age range, gender, native language, professional experience).\n\n'
            'DATA PROCESSING AND STORAGE\n'
            'During the session, data is recorded on this MacBook. The session takes place on a '
            'clean dedicated study machine with no personal accounts, applications, or notifications. '
            'Internet access is available because some tasks require it (research, online tools), but '
            'no content of web pages, messages, or any tools you use is captured. Only metadata '
            'is logged (which applications were active, when network requests occurred, but not what '
            'was requested or returned).\n'
            'After the session, the video is uploaded to a Microsoft Azure cloud server located in Spain '
            '(within the European Union) for facial expression analysis. All other data remains on the '
            'MacBook. Video is held on Azure only for the duration of processing (typically 4-8 hours) '
            'and is deleted afterwards. Microsoft acts as a sub-processor under its standard EU data '
            'protection terms. Your data never leaves the European Economic Area.\n\n'
            'PSEUDONYMIZATION\n'
            'Your name is replaced with a pseudonymous study identifier (e.g., P03). The mapping between '
            'your name and your study ID is stored on this MacBook only and is never shared with cloud '
            'processing or with anyone outside the research team.\n\n'
            'RETENTION\n'
            'All data is retained until six months after the thesis defense, then permanently deleted. '
            'If you withdraw from the study, your session data will be deleted within 72 hours.\n\n'
            'YOUR RIGHTS UNDER THE GDPR\n'
            'You can withdraw at any moment during the session by clicking the Withdraw button. You can '
            'withdraw from the study at any time afterwards by contacting the researcher. You have the '
            'right to access your data, request correction or deletion, withdraw consent, and lodge a '
            'complaint with the supervisory authority (CNPD in Portugal, BfDI in Germany).\n\n'
            'RISKS AND BENEFITS\n'
            'There are no known physical risks beyond the minor discomfort of wearing the chest strap. '
            'There are no direct benefits to you from participating, beyond contributing to research '
            'that may benefit understanding of attention and affect in knowledge work.\n\n'
            'CONTACT\n'
            'Researcher:  Norton Amato  thesis@nrtn.co\n'
            'Institution:  Berlin School of Creative Leadership / Steinbeis University Berlin\n'
            'Academic Supervisor:  Yoan Tanasale  y.tanasale@steinbeis-next.de\n'
            'Data Protection Officer:  datenschutz@steinbeis.de\n\n'
            'LEGAL BASIS\n'
            'By typing your full name below and checking the two acknowledgment boxes, you are providing '
            'legally valid digital consent under EU electronic identification regulations (eIDAS, '
            'Regulation 910/2014, Article 25). A copy of this consent record is saved on this MacBook '
            'and is available to you on request.'
        ),
        'consent_signature_label': 'Type your full name (digital signature)',
        'consent_ack_understood': 'I have read and understood the information above',
        'consent_ack_voluntary':  'My participation is voluntary and I can withdraw at any time',
        'consent_confirm':        'Provide consent and continue',
        'consent_decline':        'I do not wish to participate',
        'consent_declined_title': 'No problem.',
        'consent_declined_body':  'Please let the researcher know. No data has been recorded.',
        'consent_declined_close': 'Close',
        'consent_check': 'Participant has read and signed the consent form',
        # Demographics
        'demo_title': 'One-time Demographics',
        'demo_subtitle': 'We only ask this once per participant.',
        'demo_age': 'Age range',
        'demo_age_options': ['18-29', '30-39', '40-49', '50+'],
        'demo_gender': 'Gender',
        'demo_gender_options': ['Female', 'Male', 'Non-binary', 'Prefer not to say'],
        'demo_native_lang': 'Native language',
        'demo_native_lang_options': ['Portuguese', 'English', 'Other'],
        'demo_experience': 'Years of professional knowledge-work experience',
        'demo_experience_options': ['<1', '1-3', '4-7', '8-15', '15+'],
        # Baseline
        'baseline_title': 'Baseline Check-in',
        'baseline_subtitle': 'A few quick questions about how you are right now.',
        'baseline_sleep': 'How many hours did you sleep last night?',
        'baseline_caffeine': 'Have you had caffeine in the last 2 hours?',
        # canonical values (stored in JSONL); never change between languages
        'baseline_caffeine_options': ['None', 'Coffee', 'Tea', 'Energy drink', 'Other'],
        # display labels (what the participant sees); per-language
        'baseline_caffeine_labels':  ['None', 'Coffee', 'Tea', 'Energy drink', 'Other'],
        'baseline_mood': 'How is your overall mood right now?',
        'baseline_mood_low': 'very negative',
        'baseline_mood_high': 'very positive',
        'baseline_energy': 'How is your energy level right now?',
        'baseline_energy_low': 'very low',
        'baseline_energy_high': 'very high',
        'baseline_discomfort': 'Any physical discomfort right now?',
        'baseline_discomfort_options': ['No', 'Yes'],
        'baseline_discomfort_labels':  ['No', 'Yes'],
        'baseline_discomfort_text': '(optional: briefly describe)',
        # Likert in-session prompt (rendered as modal overlay during running)
        'likert_window_title': 'Session Check-in',
        'likert_title': 'Session Check-in',
        'likert_context': (
            'A brief check-in. Please answer all three questions based on how '
            "you've felt over the past minute or so of work. There are no right "
            'or wrong answers; your honest response is what helps the study.'
        ),
        'likert_questions': {
            'focus':       'How focused are you right now?',
            'frustration': 'How frustrated are you right now?',
            'effort':      'How much mental effort are you putting in?',
        },
        'likert_anchors': {
            'focus':       ('completely distracted',  'completely absorbed'),
            'frustration': ('not at all frustrated',  'extremely frustrated'),
            'effort':      ('no mental effort',       'maximum mental effort'),
        },
        'likert_submit': 'Submit',
        'likert_skip':   'Skip this check-in (recorded as no response)',
        # Demographics in-session prompt (rendered near end of session)
        'demo_modal_window_title': 'About You',
        'demo_modal_title': 'A few questions about you',
        'demo_modal_context': (
            'Almost done. These four quick questions help us understand the '
            'group of people taking part in the study. Your answers are '
            'attached to your study ID only, never to your name.'
        ),
        # Note: canonical option values are reused from demo_*_options;
        # display labels are demo_*_labels for languages where they differ.
        'demo_age_labels':         ['18-29', '30-39', '40-49', '50+'],
        'demo_gender_labels':      ['Female', 'Male', 'Non-binary', 'Prefer not to say'],
        'demo_native_lang_labels': ['Portuguese', 'English', 'Other'],
        'demo_experience_labels':  ['<1', '1-3', '4-7', '8-15', '15+'],
        'demo_modal_submit': 'Submit',
        # Equipment check (automatic)
        'equip_title': 'Equipment Check',
        'equip_subtitle': 'Running automatic checks. The session starts as soon as everything passes.',
        'equip_check_labels': {
            'ac_power':      'AC Power',
            'osqueryd':      'osqueryd running',
            'obs_websocket': 'OBS WebSocket',
        },
        'equip_status_pass':    'PASS',
        'equip_status_fail':    'FAIL',
        'equip_status_unknown': 'CHECK MANUALLY',
        'equip_retry_msg': 'Issues detected. Fix the items above and the check will rerun automatically.',
        'equip_retry_in': 'Retrying in {n} s...',
        'equip_starting_in': 'All checks passed. Starting session in {n}...',
        'equip_cancel': 'Cancel and go back',
        # Running
        'running_title': 'Session in Progress',
        'running_subtitle': 'Do not close this window. Streams are recording.',
        'running_streams': 'Streams running',
        'running_streams_ok': 'all healthy',
        'running_streams_warn': 'one or more streams may have stopped',
        'running_waiting_for_survey': 'Timer ended. Waiting for participant to finish the questionnaire...',
        'running_finalizing': 'Finalizing session data (saving recording and telemetry)...',
        'running_abort': 'Abort Session',
        'running_abort_confirm_title': 'Abort session?',
        'running_abort_confirm_msg': 'This will stop all streams immediately and discard the recording. Are you sure?',
        # Complete
        'complete_title': 'Session Complete',
        'complete_subtitle': 'All streams have stopped. Data is in:',
        'complete_summary': 'Summary',
        'complete_duration': 'Duration',
        'complete_exit_code': 'Session subprocess exit code',
        'complete_prompts': 'Prompts',
        # Post-session debrief (participant-facing, Step 7b)
        'debrief_title': 'Session Complete',
        'debrief_subtitle': (
            'Thank you. Before we wrap up, just a few short reflection '
            'questions about your experience.'
        ),
        'debrief_difficulty': 'How difficult did you find the tasks?',
        'debrief_difficulty_low':  'very easy',
        'debrief_difficulty_high': 'very hard',
        'debrief_disruption': 'How disruptive did you find the check-in questions?',
        'debrief_disruption_low':  'not at all',
        'debrief_disruption_high': 'extremely',
        'debrief_purpose': 'What do you think we were measuring in this session?',
        'debrief_purpose_hint': '(optional, any guess is welcome)',
        'debrief_other': 'Anything else you would like us to know?',
        'debrief_other_hint': '(optional)',
        'debrief_submit': 'Finish',
        # Consolidation summary (operator-facing)
        'consolidation_title': 'Data Saved',
        'consolidation_subtitle': 'Validating session files before closing.',
        'consolidation_all_ok': 'All expected files are present and look healthy.',
        'consolidation_issues': 'Some files have issues. See details below.',
        'consolidation_close': 'Close',
        'complete_files': 'Files written',
        'complete_next': 'Step 7b (post-session debrief) is not yet built. For now, proceed to USB transfer and AU processing.',
        'complete_finish': 'Finish',
    },
    'pt': {
        # Window
        'window_title': 'Controlador de Sessão Fase 1',
        # Common
        'back':         'Voltar',
        'continue':     'Continuar',
        'cancel':       'Cancelar',
        # Splash
        'splash_title': 'Configuração da Sessão',
        'splash_subtitle': 'Operador: por favor preencha antes de entregar o portátil ao participante.',
        'participant_name': 'Nome do participante',
        'participant_name_hint': '(apenas para registo do operador; mapeado para um ID pseudónimo do estudo)',
        'id_preview_new': 'ID do estudo:  {pid}  (novo participante)',
        'id_preview_existing': 'ID do estudo:  {pid}  (participante existente, dados demográficos serão reutilizados)',
        'id_preview_empty': 'ID do estudo:  —  (introduza um nome acima)',
        'language_label': 'Idioma do questionário para esta sessão',
        # Digital consent (participant-facing, replaces paper)
        'consent_title': 'Consentimento Informado',
        'consent_subtitle': 'Por favor leia com atenção. Escreva o seu nome completo e marque as duas caixas no fundo para dar o seu consentimento.',
        'consent_text': (
            'ESTUDO\n'
            'Estudo de Calibração Fase 1 — Telemetria Comportamental como Instrumento Afetivo\n'
            'Investigação de tese de mestrado, Berlin School of Creative Leadership / Steinbeis University Berlin.\n\n'
            'OBJETIVO\n'
            'Este estudo examina se a telemetria comportamental de software típico de trabalho cognitivo '
            '(ritmo de digitação, atividade do rato, aplicação em foco, eventos do sistema) pode ser '
            'combinada com sinais fisiológicos (frequência cardíaca) e análise de expressões faciais '
            'para detetar mudanças de atenção, foco e estado emocional durante o trabalho.\n\n'
            'O QUE VAI FAZER\n'
            'Irá realizar uma sessão de trabalho cognitivo de 26 minutos envolvendo leitura, análise de '
            'dados em Microsoft Excel e escrita de síntese. Irá usar uma banda peitoral Polar H10 que mede '
            'a frequência cardíaca. Ser-lhe-ão feitas três breves perguntas de verificação aproximadamente '
            'aos minutos 4, 12 e 19. Perto do fim da sessão, ser-lhe-ão feitas quatro breves perguntas '
            'demográficas (apenas na primeira sessão).\n\n'
            'DADOS RECOLHIDOS\n'
            '• Frequência cardíaca e variabilidade da frequência cardíaca da banda peitoral.\n'
            '• Apenas o ritmo de digitação — NÃO o conteúdo do que escreve.\n'
            '• Movimento e cliques do rato.\n'
            '• Aplicação em foco em cada momento.\n'
            '• Eventos de atividade do sistema (lançamento de processos, eventos de ficheiros e rede).\n'
            '• Gravação de vídeo do seu rosto para análise de expressões faciais (unidades de ação, pose).\n'
            '• As suas respostas Likert às verificações durante a sessão.\n'
            '• Os seus dados demográficos (faixa etária, género, língua materna, experiência profissional).\n\n'
            'PROCESSAMENTO E ARMAZENAMENTO DE DADOS\n'
            'Durante a sessão, os dados são gravados neste MacBook. A sessão decorre numa máquina '
            'dedicada e limpa, sem contas pessoais, aplicações ou notificações. O acesso à internet '
            'está disponível porque algumas tarefas o exigem (pesquisa, ferramentas online), mas '
            'nenhum conteúdo de páginas web, mensagens ou ferramentas que utilizar é capturado. '
            'Apenas metadados são registados (que aplicações estavam ativas, quando ocorreram pedidos '
            'de rede, mas não o que foi pedido ou devolvido).\n'
            'Após a sessão, o vídeo é enviado para um servidor Microsoft Azure localizado em Espanha '
            '(dentro da União Europeia) para análise de expressões faciais. Todos os outros dados '
            'permanecem no MacBook. O vídeo é mantido no Azure apenas durante o processamento '
            '(tipicamente 4-8 horas) e é eliminado depois. A Microsoft atua como subcontratante segundo '
            'os seus termos padrão de proteção de dados na UE. Os seus dados nunca saem do Espaço '
            'Económico Europeu.\n\n'
            'PSEUDONIMIZAÇÃO\n'
            'O seu nome é substituído por um identificador pseudónimo de estudo (por ex.: P03). O '
            'mapeamento entre o seu nome e o ID de estudo é armazenado apenas neste MacBook e nunca '
            'é partilhado com o processamento na nuvem nem com ninguém fora da equipa de investigação.\n\n'
            'RETENÇÃO\n'
            'Todos os dados são mantidos até seis meses após a defesa da tese e depois permanentemente '
            'eliminados. Se desistir do estudo, os dados da sua sessão serão eliminados em 72 horas.\n\n'
            'OS SEUS DIREITOS AO ABRIGO DO RGPD\n'
            'Pode desistir a qualquer momento durante a sessão clicando no botão Desistir. Pode desistir '
            'do estudo a qualquer momento depois, contactando o investigador. Tem o direito de aceder '
            'aos seus dados, pedir correção ou eliminação, retirar o consentimento e apresentar queixa '
            'à autoridade de controlo (CNPD em Portugal, BfDI na Alemanha).\n\n'
            'RISCOS E BENEFÍCIOS\n'
            'Não há riscos físicos conhecidos para além do desconforto menor de usar a banda peitoral. '
            'Não há benefícios diretos para si pela participação, para além de contribuir para '
            'investigação que pode beneficiar a compreensão de atenção e afeto no trabalho cognitivo.\n\n'
            'CONTACTO\n'
            'Investigador:  Norton Amato  thesis@nrtn.co\n'
            'Instituição:  Berlin School of Creative Leadership / Steinbeis University Berlin\n'
            'Orientador Académico:  Yoan Tanasale  y.tanasale@steinbeis-next.de\n'
            'Encarregado de Proteção de Dados:  datenschutz@steinbeis.de\n\n'
            'BASE LEGAL\n'
            'Ao escrever o seu nome completo abaixo e marcar as duas caixas de reconhecimento, está a '
            'fornecer consentimento digital legalmente válido ao abrigo dos regulamentos europeus de '
            'identificação eletrónica (eIDAS, Regulamento 910/2014, Artigo 25). Uma cópia deste registo '
            'de consentimento é guardada neste MacBook e está disponível para si mediante pedido.'
        ),
        'consent_signature_label': 'Escreva o seu nome completo (assinatura digital)',
        'consent_ack_understood': 'Li e compreendi a informação acima',
        'consent_ack_voluntary':  'A minha participação é voluntária e posso desistir a qualquer momento',
        'consent_confirm':        'Dar consentimento e continuar',
        'consent_decline':        'Não desejo participar',
        'consent_declined_title': 'Sem problema.',
        'consent_declined_body':  'Por favor informe o investigador. Nenhum dado foi gravado.',
        'consent_declined_close': 'Fechar',
        'consent_check': 'O participante leu e assinou o formulário de consentimento',
        # Demographics
        'demo_title': 'Dados Demográficos (uma só vez)',
        'demo_subtitle': 'Apenas perguntamos isto uma vez por participante.',
        'demo_age': 'Faixa etária',
        'demo_age_options': ['18-29', '30-39', '40-49', '50+'],
        'demo_gender': 'Género',
        # canonical values match EN; PT labels via demo_gender_labels
        'demo_gender_options': ['Female', 'Male', 'Non-binary', 'Prefer not to say'],
        'demo_native_lang': 'Língua materna',
        'demo_native_lang_options': ['Portuguese', 'English', 'Other'],
        'demo_experience': 'Anos de experiência em trabalho de escritório ou intelectual',
        'demo_experience_options': ['<1', '1-3', '4-7', '8-15', '15+'],
        # Baseline
        'baseline_title': 'Verificação Inicial',
        'baseline_subtitle': 'Algumas perguntas rápidas sobre como se sente agora.',
        'baseline_sleep': 'Quantas horas dormiu na noite passada?',
        'baseline_caffeine': 'Consumiu cafeína nas últimas 2 horas?',
        # canonical values are in English (stored in JSONL); display in PT
        'baseline_caffeine_options': ['None', 'Coffee', 'Tea', 'Energy drink', 'Other'],
        'baseline_caffeine_labels':  ['Nenhuma', 'Café', 'Chá', 'Bebida energética', 'Outra'],
        'baseline_mood': 'Como descreveria o seu humor neste momento?',
        'baseline_mood_low': 'muito negativo',
        'baseline_mood_high': 'muito positivo',
        'baseline_energy': 'Como está o seu nível de energia neste momento?',
        'baseline_energy_low': 'muito baixo',
        'baseline_energy_high': 'muito alto',
        'baseline_discomfort': 'Algum desconforto físico neste momento?',
        'baseline_discomfort_options': ['No', 'Yes'],
        'baseline_discomfort_labels':  ['Não', 'Sim'],
        'baseline_discomfort_text': '(opcional: descreva brevemente)',
        # Likert in-session prompt
        'likert_window_title': 'Verificação Rápida',
        'likert_title': 'Verificação Rápida',
        'likert_context': (
            'Uma verificação rápida. Por favor, responda às três perguntas '
            'com base em como se sentiu no último minuto de trabalho. Não '
            'há respostas certas ou erradas; a sua resposta honesta é o que '
            'ajuda o estudo.'
        ),
        'likert_questions': {
            'focus':       'Quão focado(a) está agora?',
            'frustration': 'Quão frustrado(a) está agora?',
            'effort':      'Quanto esforço mental está a dedicar?',
        },
        'likert_anchors': {
            'focus':       ('completamente distraído(a)',
                            'completamente absorvido(a)'),
            'frustration': ('nada frustrado(a)',
                            'extremamente frustrado(a)'),
            'effort':      ('nenhum esforço mental',
                            'esforço mental máximo'),
        },
        'likert_submit': 'Enviar',
        'likert_skip':   'Saltar esta verificação (registada como sem resposta)',
        # Demographics in-session prompt
        'demo_modal_window_title': 'Sobre Si',
        'demo_modal_title': 'Algumas perguntas sobre si',
        'demo_modal_context': (
            'Quase no fim. Estas quatro perguntas rápidas ajudam-nos a '
            'compreender o grupo de pessoas que participa neste estudo. '
            'As suas respostas ficam associadas apenas ao ID do estudo, '
            'nunca ao seu nome.'
        ),
        'demo_age_labels':         ['18-29', '30-39', '40-49', '50+'],
        'demo_gender_labels':      ['Feminino', 'Masculino', 'Não-binário', 'Prefiro não responder'],
        'demo_native_lang_labels': ['Português', 'Inglês', 'Outra'],
        'demo_experience_labels':  ['<1', '1-3', '4-7', '8-15', '15+'],
        'demo_modal_submit': 'Enviar',
        # Equipment check (automatic)
        'equip_title': 'Verificação do Equipamento',
        'equip_subtitle': 'A executar verificações automáticas. A sessão começa assim que tudo estiver OK.',
        'equip_check_labels': {
            'ac_power':      'Alimentação CA',
            'osqueryd':      'osqueryd em execução',
            'obs_websocket': 'OBS WebSocket',
        },
        'equip_status_pass':    'OK',
        'equip_status_fail':    'FALHA',
        'equip_status_unknown': 'VERIFICAR MANUALMENTE',
        'equip_retry_msg': 'Problemas detetados. Corrija os itens acima e a verificação será repetida automaticamente.',
        'equip_retry_in': 'A repetir em {n} s...',
        'equip_starting_in': 'Todas as verificações OK. A iniciar sessão em {n}...',
        'equip_cancel': 'Cancelar e voltar atrás',
        # Running
        'running_title': 'Sessão em Curso',
        'running_subtitle': 'Não feche esta janela. Os fluxos estão a gravar.',
        'running_streams': 'Fluxos em execução',
        'running_streams_ok': 'todos saudáveis',
        'running_streams_warn': 'um ou mais fluxos podem ter parado',
        'running_waiting_for_survey': 'O temporizador terminou. A aguardar que o participante conclua o questionário...',
        'running_finalizing': 'A finalizar os dados da sessão (a guardar gravação e telemetria)...',
        'running_abort': 'Abortar Sessão',
        'running_abort_confirm_title': 'Abortar sessão?',
        'running_abort_confirm_msg': 'Isto irá parar todos os fluxos imediatamente e descartar a gravação. Tem a certeza?',
        # Complete
        'complete_title': 'Sessão Concluída',
        'complete_subtitle': 'Todos os fluxos pararam. Os dados estão em:',
        'complete_summary': 'Resumo',
        'complete_duration': 'Duração',
        'complete_exit_code': 'Código de saída do subprocesso da sessão',
        'complete_prompts': 'Verificações',
        # Post-session debrief (participant-facing, Step 7b)
        'debrief_title': 'Sessão Concluída',
        'debrief_subtitle': (
            'Obrigado. Antes de terminarmos, apenas algumas perguntas '
            'rápidas de reflexão sobre a sua experiência.'
        ),
        'debrief_difficulty': 'Quão difíceis achou as tarefas?',
        'debrief_difficulty_low':  'muito fáceis',
        'debrief_difficulty_high': 'muito difíceis',
        'debrief_disruption': 'Quão disruptivas achou as perguntas de verificação?',
        'debrief_disruption_low':  'nada',
        'debrief_disruption_high': 'extremamente',
        'debrief_purpose': 'O que pensa que estávamos a medir nesta sessão?',
        'debrief_purpose_hint': '(opcional, qualquer suposição é bem-vinda)',
        'debrief_other': 'Algo mais que gostaria de partilhar connosco?',
        'debrief_other_hint': '(opcional)',
        'debrief_submit': 'Concluir',
        # Consolidation summary
        'consolidation_title': 'Dados Guardados',
        'consolidation_subtitle': 'A validar ficheiros da sessão antes de fechar.',
        'consolidation_all_ok': 'Todos os ficheiros esperados estão presentes e parecem saudáveis.',
        'consolidation_issues': 'Alguns ficheiros têm problemas. Ver detalhes abaixo.',
        'consolidation_close': 'Fechar',
        'complete_files': 'Ficheiros gravados',
        'complete_next': 'O Passo 7b (debrief pós-sessão) ainda não foi construído. Por agora, prossiga para transferência USB e processamento AU.',
        'complete_finish': 'Concluir',
    },
}


# =============================================================================
# Helpers
# =============================================================================

def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def now_session_id(participant_id):
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return f'{participant_id}_{ts}'


def has_demographics(participant_id):
    return (PARTICIPANTS_DIR / participant_id / 'demographics.json').exists()


def load_demographics(participant_id):
    path = PARTICIPANTS_DIR / participant_id / 'demographics.json'
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_demographics(participant_id, demographics):
    pdir = PARTICIPANTS_DIR / participant_id
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / 'demographics.json').write_text(
        json.dumps(demographics, indent=2, ensure_ascii=False)
    )


def write_session_metadata(session_dir, metadata):
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / 'session_metadata.json'
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))


# -----------------------------------------------------------------------------
# In-process JSONL logging for survey prompts
# -----------------------------------------------------------------------------

def append_survey_jsonl(session_dir, obj):
    """Append one JSON object to survey.jsonl. Flush + fsync for durability."""
    path = session_dir / 'survey.jsonl'
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


# -----------------------------------------------------------------------------
# Audio cue for timer end
# -----------------------------------------------------------------------------

def play_timer_end_sound():
    """
    Play a brief, non-alarming chime to signal the timer has ended.
    Uses macOS's built-in afplay with the system Glass.aiff sound.
    Fails silently if afplay isn't available or the sound file is missing
    (e.g. on a non-macOS host); the visual countdown reaching 00:00 is
    still the primary signal.
    """
    sound_path = '/System/Library/Sounds/Glass.aiff'
    try:
        # Spawn detached; we don't wait for it
        subprocess.Popen(
            ['afplay', sound_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        pass


def compute_prompt_schedule(session_start_t, prompt_times_min,
                              jitter_sec, demographics_at_min=None,
                              rng=None):
    """
    Build the list of scheduled prompts for the session.

    Returns: list of dicts {nominal_min, actual_offset_sec, fire_ts,
                            prompt_type}
    """
    if rng is None:
        rng = random.Random()
    schedule = []
    for nominal_min in prompt_times_min:
        jitter = rng.uniform(-jitter_sec, jitter_sec)
        actual_sec = nominal_min * 60.0 + jitter
        schedule.append({
            'nominal_min': nominal_min,
            'actual_offset_sec': actual_sec,
            'fire_ts': session_start_t + actual_sec,
            'prompt_type': 'likert',
        })
    if demographics_at_min is not None:
        actual_sec = demographics_at_min * 60.0
        schedule.append({
            'nominal_min': demographics_at_min,
            'actual_offset_sec': actual_sec,
            'fire_ts': session_start_t + actual_sec,
            'prompt_type': 'demographics',
        })
    schedule.sort(key=lambda x: x['fire_ts'])
    return schedule


# -----------------------------------------------------------------------------
# Participant ID auto-generation
# -----------------------------------------------------------------------------
# Operator enters the participant's name; the system maps it to a pseudonymous
# study ID (P01, P02, ...). The name lives only in the local _index.json on
# the lab Mac. Session data on disk references the ID only, so no PII leaves
# the lab machine when sessions are transferred to PC or Azure for processing.

PARTICIPANT_INDEX = PARTICIPANTS_DIR / '_index.json'
CONSENT_DECLINES_LOG = THESIS_DIR / 'consent_declines.jsonl'


def _normalize_name(name):
    """Lowercase, collapse whitespace. Used as the lookup key."""
    return ' '.join(name.lower().split())


def _load_participant_index():
    if PARTICIPANT_INDEX.exists():
        try:
            return json.loads(PARTICIPANT_INDEX.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def lookup_participant_id(name):
    """Return existing ID for this name, or None if not yet assigned."""
    if not name or not name.strip():
        return None
    index = _load_participant_index()
    return index.get(_normalize_name(name))


def preview_participant_id(name):
    """
    Return (id_str, is_existing) the ID that would be assigned/reused for
    this name. Non-destructive: does not write the index. Returns (None, None)
    for empty input.
    """
    if not name or not name.strip():
        return None, None
    normalized = _normalize_name(name)
    index = _load_participant_index()
    if normalized in index:
        return index[normalized], True

    # Find next available number, accounting for both index entries and
    # any participant directories that may exist independently
    used = set()
    for existing_id in index.values():
        if existing_id.startswith('P') and existing_id[1:].isdigit():
            used.add(int(existing_id[1:]))
    if PARTICIPANTS_DIR.exists():
        for d in PARTICIPANTS_DIR.iterdir():
            if d.is_dir() and d.name.startswith('P') and d.name[1:].isdigit():
                used.add(int(d.name[1:]))
    n = 1
    while n in used:
        n += 1
    return f'P{n:02d}', False


def log_consent_decline(participant_id, language):
    """Append a single record when a participant declines reconfirmation."""
    THESIS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'timestamp_utc': now_utc_iso(),
        'participant_id': participant_id,
        'language': language,
    }
    with open(CONSENT_DECLINES_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def assign_participant_id(name):
    """Persist the name -> ID mapping. Returns the assigned ID."""
    pid, is_existing = preview_participant_id(name)
    if pid is None:
        raise ValueError('Cannot assign ID for empty name')
    if is_existing:
        return pid
    index = _load_participant_index()
    index[_normalize_name(name)] = pid
    PARTICIPANT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    PARTICIPANT_INDEX.write_text(
        json.dumps(index, indent=2, ensure_ascii=False)
    )
    return pid


# -----------------------------------------------------------------------------
# Automatic equipment checks (macOS)
# -----------------------------------------------------------------------------
# Each check returns (status, message) where status is:
#   True  = pass
#   False = fail (blocks session start)
#   None  = could not verify (does not block; operator confirms manually)

def check_ac_power():
    try:
        r = subprocess.run(['pmset', '-g', 'batt'],
                           capture_output=True, text=True, timeout=5)
        if 'AC Power' in r.stdout:
            return True, 'Connected to AC power'
        return False, 'Running on battery: connect to power adapter'
    except Exception as e:
        return None, f'Could not check power state: {e}'


def check_wifi_off():
    try:
        r = subprocess.run(['networksetup', '-getairportpower', 'en0'],
                           capture_output=True, text=True, timeout=5)
        if 'Off' in r.stdout:
            return True, 'Wi-Fi disabled (offline acquisition OK)'
        return False, 'Wi-Fi is on: please disable for offline protocol'
    except Exception as e:
        return None, f'Could not check Wi-Fi state: {e}'


def check_osqueryd():
    try:
        r = subprocess.run(['pgrep', '-l', 'osqueryd'],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return True, 'osqueryd process is running'
        return False, 'osqueryd not running: start it before the session'
    except Exception as e:
        return None, f'Could not check osquery: {e}'


def check_obs_websocket(port=4455):
    import socket
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            return True, f'OBS WebSocket reachable on port {port}'
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False, f'OBS WebSocket not reachable on port {port}: is OBS running with WebSocket enabled?'


EQUIPMENT_CHECKS = [
    ('ac_power',      check_ac_power),
    ('osqueryd',      check_osqueryd),
    ('obs_websocket', check_obs_websocket),
]


# =============================================================================
# Custom Likert button group (reused style from survey_prompter.py)
# =============================================================================

class LikertButtonGroup:
    """7-point Likert row, click-twice-to-unset behavior."""

    def __init__(self, parent, var, size=44, font_size=15):
        self.var = var
        self.cells = []
        self.frame = tk.Frame(parent, bg=COLOR_BG_WINDOW)
        for i in range(1, 8):
            cell = tk.Frame(
                self.frame, width=size, height=size,
                bg=COLOR_BG_BUTTON,
                highlightthickness=1,
                highlightbackground=COLOR_BORDER_BUTTON,
                cursor='hand2',
            )
            cell.pack_propagate(False)
            cell.pack(side='left', padx=4)
            label = tk.Label(
                cell, text=str(i),
                font=('Helvetica', font_size, 'bold'),
                bg=COLOR_BG_BUTTON, fg=COLOR_FG_BUTTON,
                cursor='hand2',
            )
            label.pack(expand=True, fill='both')
            handler = self._make_handler(i)
            cell.bind('<Button-1>', handler)
            label.bind('<Button-1>', handler)
            self.cells.append({'frame': cell, 'label': label, 'value': i})
        var.trace_add('write', self._refresh)

    def _make_handler(self, value):
        def handler(_e):
            self.var.set(0 if self.var.get() == value else value)
        return handler

    def _refresh(self, *_a):
        sel = self.var.get()
        for c in self.cells:
            if c['value'] == sel:
                c['frame'].config(bg=COLOR_BG_SELECTED)
                c['label'].config(bg=COLOR_BG_SELECTED, fg=COLOR_FG_SELECTED)
            else:
                c['frame'].config(bg=COLOR_BG_BUTTON)
                c['label'].config(bg=COLOR_BG_BUTTON, fg=COLOR_FG_BUTTON)

    def pack(self, **kw):
        self.frame.pack(**kw)


class BorderedEntry:
    """
    tk.Entry wrapped in a colored Frame to give it a visible border on macOS.

    macOS Tk ignores `highlightthickness`/`highlightbackground` on Entry
    widgets (same issue as tk.Button), so the only reliable way to get a
    visible border is to put the Entry inside a Frame whose background
    color shows through 2px of padding around it. Border turns blue on
    focus, gray when not.
    """

    BORDER_PX = 2

    def __init__(self, parent, textvariable=None, font=('Helvetica', 13),
                 width=None, justify='left', validate=None):
        """
        validate: optional string. 'numeric' accepts only digits and one
        decimal point (rejects letters and symbols at the keystroke level).
        """
        self.frame = tk.Frame(parent, bg=COLOR_BORDER_BUTTON,
                              highlightthickness=0)
        kwargs = {
            'textvariable': textvariable,
            'font': font,
            'bg': '#F9FAFB',
            'fg': COLOR_FG_BUTTON,
            'insertbackground': COLOR_FG_BUTTON,
            'relief': 'flat',
            'bd': 0,
            'highlightthickness': 0,
            'justify': justify,
        }
        if width is not None:
            kwargs['width'] = width
        self.entry = tk.Entry(self.frame, **kwargs)

        if validate == 'numeric':
            def _is_numeric(proposed):
                # Allow empty (so backspace works) and any prefix that parses
                # as float. Rejects letters, multiple dots, etc.
                if proposed == '' or proposed == '.':
                    return True
                try:
                    float(proposed)
                    return True
                except ValueError:
                    return False
            vcmd = (self.entry.register(_is_numeric), '%P')
            self.entry.config(validate='key', validatecommand=vcmd)

        self.entry.pack(padx=self.BORDER_PX, pady=self.BORDER_PX,
                         ipady=6, fill='x', expand=True)
        self.entry.bind('<FocusIn>',
                        lambda _e: self.frame.config(bg=COLOR_BG_SELECTED))
        self.entry.bind('<FocusOut>',
                        lambda _e: self.frame.config(bg=COLOR_BORDER_BUTTON))

    def pack(self, **kw):
        self.frame.pack(**kw)

    def pack_forget(self):
        self.frame.pack_forget()

    def get(self):
        return self.entry.get()

    def insert(self, idx, text):
        self.entry.insert(idx, text)

    def delete(self, first, last=None):
        if last is None:
            self.entry.delete(first)
        else:
            self.entry.delete(first, last)

    def bind(self, event, callback):
        self.entry.bind(event, callback)


class OptionButtonGroup:
    """
    Row of clickable option buttons backed by a StringVar.

    Same visual language as LikertButtonGroup: gray cells with dark text
    when unselected, solid blue with white text when selected. Buttons
    auto-size to their text content. Click the currently-selected button
    again to deselect (StringVar goes back to '').

    The button labels (what the participant sees) can differ from the
    canonical values (what gets stored in the StringVar / written to logs).
    This lets a single canonical English vocabulary be displayed in any
    language without changing the analyst-facing data.

    Use for categorical single-select questions: demographics, caffeine,
    discomfort yes/no, etc.
    """

    def __init__(self, parent, var, options, display_labels=None,
                 font_size=12, padx_label=16, pady_label=10):
        self.var = var
        self.options = list(options)
        self.display_labels = (list(display_labels)
                                if display_labels is not None
                                else list(options))
        if len(self.display_labels) != len(self.options):
            raise ValueError('display_labels must match options length')
        self.cells = []
        self.frame = tk.Frame(parent, bg=COLOR_BG_WINDOW)
        for canonical, label_text in zip(self.options, self.display_labels):
            cell = tk.Frame(
                self.frame,
                bg=COLOR_BG_BUTTON,
                highlightthickness=1,
                highlightbackground=COLOR_BORDER_BUTTON,
                cursor='hand2',
            )
            cell.pack(side='left', padx=4)
            label = tk.Label(
                cell, text=label_text,
                font=('Helvetica', font_size),
                bg=COLOR_BG_BUTTON, fg=COLOR_FG_BUTTON,
                padx=padx_label, pady=pady_label,
                cursor='hand2',
            )
            label.pack()
            handler = self._make_handler(canonical)
            cell.bind('<Button-1>', handler)
            label.bind('<Button-1>', handler)
            self.cells.append({'frame': cell, 'label': label,
                                'value': canonical})
        var.trace_add('write', self._refresh)

    def _make_handler(self, value):
        def handler(_e):
            self.var.set('' if self.var.get() == value else value)
        return handler

    def _refresh(self, *_a):
        sel = self.var.get()
        for c in self.cells:
            if c['value'] == sel:
                c['frame'].config(bg=COLOR_BG_SELECTED)
                c['label'].config(bg=COLOR_BG_SELECTED, fg=COLOR_FG_SELECTED)
            else:
                c['frame'].config(bg=COLOR_BG_BUTTON)
                c['label'].config(bg=COLOR_BG_BUTTON, fg=COLOR_FG_BUTTON)

    def set_display_labels(self, new_labels):
        """Update displayed labels without resetting the selection."""
        if len(new_labels) != len(self.cells):
            raise ValueError('new_labels must match number of cells')
        for cell, new_label in zip(self.cells, new_labels):
            cell['label'].config(text=new_label)
        self.display_labels = list(new_labels)

    def pack(self, **kw):
        self.frame.pack(**kw)


# =============================================================================
# Pretty primary button (Frame+Label to bypass macOS native rendering)
# =============================================================================

class PrimaryButton:
    """Big green Submit-style button. State managed by .set_enabled(bool)."""

    def __init__(self, parent, text, command, width_padx=44):
        self.command = command
        self.enabled = False
        self.frame = tk.Frame(parent, bg=COLOR_BG_PRIMARY_OFF,
                              highlightthickness=0, cursor='arrow')
        self.label = tk.Label(
            self.frame, text=text,
            font=('Helvetica', 14, 'bold'),
            bg=COLOR_BG_PRIMARY_OFF, fg=COLOR_FG_PRIMARY_OFF,
            padx=width_padx, pady=12, cursor='arrow',
        )
        self.label.pack()
        for w in (self.frame, self.label):
            w.bind('<Button-1>', self._on_click)
            w.bind('<Enter>', self._on_enter)
            w.bind('<Leave>', self._on_leave)

    def _on_click(self, _e):
        if self.enabled:
            self.command()

    def _on_enter(self, _e):
        if self.enabled:
            self.frame.config(bg=COLOR_BG_PRIMARY_HOT)
            self.label.config(bg=COLOR_BG_PRIMARY_HOT)

    def _on_leave(self, _e):
        if self.enabled:
            self.frame.config(bg=COLOR_BG_PRIMARY_ON)
            self.label.config(bg=COLOR_BG_PRIMARY_ON)

    def set_enabled(self, enabled):
        self.enabled = enabled
        if enabled:
            self.frame.config(bg=COLOR_BG_PRIMARY_ON, cursor='hand2')
            self.label.config(bg=COLOR_BG_PRIMARY_ON, fg=COLOR_FG_PRIMARY_ON,
                              cursor='hand2')
        else:
            self.frame.config(bg=COLOR_BG_PRIMARY_OFF, cursor='arrow')
            self.label.config(bg=COLOR_BG_PRIMARY_OFF, fg=COLOR_FG_PRIMARY_OFF,
                              cursor='arrow')

    def set_text(self, text):
        self.label.config(text=text)

    def pack(self, **kw):
        self.frame.pack(**kw)


# =============================================================================
# Main controller
# =============================================================================

class SessionController:
    def __init__(self, duration_sec, initial_language, dry_run):
        self.duration_sec = duration_sec
        self.language = initial_language
        self.dry_run = dry_run

        # Collected state across screens
        self.participant_id = None
        self._participant_is_existing = False
        self.consent_reconfirmed_utc = None
        self.consent_reconfirmed_language = None
        self.consent_signature = None
        self.consent_text_shown = None
        self._pending_consent_record = None
        self.demographics = None
        self.baseline = None
        self.debrief = None
        self.equipment_checked = []

        # Draft state preserved across screen re-renders (Back button,
        # language toggle). Each screen reads from its draft on render
        # and writes to it on every field change.
        self._draft_splash   = {'name': ''}
        self._draft_consent  = {'signature': '',
                                 'ack_understood': False,
                                 'ack_voluntary': False}
        self._draft_baseline = {'sleep': '', 'caffeine': '',
                                 'mood': 0, 'energy': 0,
                                 'discomfort': '', 'discomfort_text': ''}
        self._draft_debrief = {'difficulty': 0, 'disruption': 0,
                                'purpose': '', 'other': ''}

        # Session runtime state
        self.session_id = None
        self.session_dir = None
        self.session_start_t = None
        self.session_proc = None
        self.survey_proc = None  # legacy kept for safety in _poll_tick checks
        self._checklist_proc = None
        self.session_exit_code = None
        # In-process prompt scheduling state (populated in _launch_session)
        self._prompt_schedule = []
        self._prompt_results = []
        self._pending_after_ids = []
        self._modal_open = False
        self._timer_end_sound_played = False

        self.root = tk.Tk()
        self.root.title(self._s()['window_title'])
        self.root.configure(bg=COLOR_BG_WINDOW)
        self.root.geometry('720x780')
        self.root.protocol('WM_DELETE_WINDOW', self._on_window_close)

        self.current_screen = None
        self._show_splash()

    # -------- Language helpers --------

    def _s(self):
        return STRINGS[self.language]

    def _switch_language(self, lang):
        if lang not in SUPPORTED_LANGUAGES or lang == self.language:
            return
        self.language = lang
        self.root.title(self._s()['window_title'])
        # Rebuild current screen with new language
        self._rerender_current()

    def _rerender_current(self):
        if self.current_screen == 'splash':              self._show_splash()
        elif self.current_screen == 'consent':           self._show_consent()
        elif self.current_screen == 'consent_declined':  self._show_consent_declined()
        elif self.current_screen == 'baseline':          self._show_baseline()
        elif self.current_screen == 'equipment':         self._show_equipment()
        elif self.current_screen == 'running':           self._show_running()
        elif self.current_screen == 'debrief':           self._show_debrief()
        elif self.current_screen == 'complete':          self._show_complete()

    # -------- Screen scaffolding --------

    def _clear(self):
        # Unbind any keyboard shortcuts the previous screen may have set,
        # so they don't bleed across screens
        try:
            self.root.unbind('<Return>')
        except Exception:
            pass
        for widget in self.root.winfo_children():
            widget.destroy()

    def _add_top_bar(self):
        bar = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        bar.pack(fill='x', padx=20, pady=(10, 0))
        for lang in ('pt', 'en'):
            lbl = tk.Label(
                bar, text=FLAGS[lang],
                font=('Helvetica',
                      FLAG_FONT_SELECTED if lang == self.language
                      else FLAG_FONT_UNSELECTED),
                bg=COLOR_BG_WINDOW, cursor='hand2',
            )
            lbl.pack(side='right', padx=4)
            lbl.bind('<Button-1>', lambda _e, l=lang: self._switch_language(l))

    def _add_title(self, text, subtitle=None):
        tk.Label(
            self.root, text=text,
            font=('Helvetica', 22, 'bold'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_TITLE,
        ).pack(pady=(8, 6), padx=24)
        if subtitle:
            tk.Label(
                self.root, text=subtitle,
                font=('Helvetica', 11), wraplength=620, justify='center',
                bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
            ).pack(pady=(0, 14), padx=24)

    def _add_section_label(self, parent, text):
        tk.Label(
            parent, text=text,
            font=('Helvetica', 13, 'bold'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
        ).pack(anchor='w', pady=(10, 4))

    # =========================================================================
    # Screen: Splash
    # =========================================================================

    def _show_splash(self):
        self.current_screen = 'splash'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['splash_title'], s['splash_subtitle'])

        form = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        form.pack(padx=40, pady=10, fill='x')

        # Participant name (operator types this; ID is auto-derived)
        self._add_section_label(form, s['participant_name'])
        name_var = tk.StringVar(value=self._draft_splash.get('name', ''))
        name_entry = BorderedEntry(form, textvariable=name_var,
                                    font=('Helvetica', 14))
        name_entry.pack(fill='x')
        tk.Label(
            form, text=s['participant_name_hint'],
            font=('Helvetica', 9, 'italic'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
        ).pack(anchor='w', pady=(2, 0))

        # Auto-generated study ID preview
        id_preview_label = tk.Label(
            form, text=s['id_preview_empty'],
            font=('Helvetica', 12, 'bold'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
        )
        id_preview_label.pack(anchor='w', pady=(14, 0))

        def update_preview():
            name = name_var.get().strip()
            if not name:
                id_preview_label.config(
                    text=s['id_preview_empty'], fg=COLOR_TEXT_MUTED,
                )
                return None, None
            pid, is_existing = preview_participant_id(name)
            if is_existing:
                id_preview_label.config(
                    text=s['id_preview_existing'].format(pid=pid),
                    fg=COLOR_BG_PRIMARY_ON,
                )
            else:
                id_preview_label.config(
                    text=s['id_preview_new'].format(pid=pid),
                    fg=COLOR_TEXT_BODY,
                )
            return pid, is_existing

        def on_form_change(*_a):
            # Persist draft on every keystroke
            self._draft_splash['name'] = name_var.get()
            pid, _ = update_preview()
            valid = bool(name_var.get().strip()) and pid is not None
            continue_btn.set_enabled(valid)

        name_var.trace_add('write', on_form_change)

        # Continue button
        def on_continue():
            name = name_var.get().strip()
            self._draft_splash['name'] = name
            self._last_typed_name = name
            # Source of truth for "is this a returning participant" is the
            # name->ID index lookup. The same answer drives whether the
            # demographics popup fires later in the session.
            _, is_existing = preview_participant_id(name)
            self._participant_is_existing = is_existing
            self.participant_id = assign_participant_id(name)
            self.demographics = load_demographics(self.participant_id)
            self._show_consent()

        continue_btn = PrimaryButton(self.root, s['continue'], on_continue)
        continue_btn.pack(pady=(40, 20))

        # Enter key advances when form is valid
        def _maybe_continue(_e):
            name = name_var.get().strip()
            if name and preview_participant_id(name)[0] is not None:
                on_continue()

        self.root.bind('<Return>', _maybe_continue)
        name_entry.entry.focus_set()

        on_form_change()

    # =========================================================================
    # Screen: Digital consent (participant-facing, replaces paper)
    # =========================================================================

    def _show_consent(self):
        self.current_screen = 'consent'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['consent_title'], s['consent_subtitle'])

        # Scrollable consent text in a Text widget with attached Scrollbar.
        # tk.Text honors fg/bg on macOS unlike tk.Entry, so no wrapper needed.
        text_outer = tk.Frame(self.root, bg=COLOR_BORDER_BUTTON,
                              highlightthickness=0)
        text_outer.pack(padx=40, pady=(0, 14), fill='both', expand=True)

        text_frame = tk.Frame(text_outer, bg='#F9FAFB')
        text_frame.pack(padx=1, pady=1, fill='both', expand=True)

        scrollbar = tk.Scrollbar(text_frame, orient='vertical')
        scrollbar.pack(side='right', fill='y')

        text_widget = tk.Text(
            text_frame,
            font=('Helvetica', 11),
            bg='#F9FAFB',
            fg=COLOR_TEXT_BODY,
            relief='flat',
            bd=0,
            highlightthickness=0,
            padx=14, pady=14,
            wrap='word',
            yscrollcommand=scrollbar.set,
            height=14,
        )
        text_widget.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=text_widget.yview)

        text_widget.insert('1.0', s['consent_text'])
        text_widget.config(state='disabled')

        # Signature row (typed name) + acknowledgments
        sig_frame = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        sig_frame.pack(padx=40, pady=(2, 4), fill='x')

        self._add_section_label(sig_frame, s['consent_signature_label'])
        signature_var = tk.StringVar(value=self._draft_consent.get('signature', ''))
        signature_entry = BorderedEntry(sig_frame, textvariable=signature_var,
                                         font=('Helvetica', 13))
        signature_entry.pack(fill='x')

        ack_understood_var = tk.BooleanVar(
            value=self._draft_consent.get('ack_understood', False))
        ack_voluntary_var = tk.BooleanVar(
            value=self._draft_consent.get('ack_voluntary', False))

        def on_change(*_a):
            # Persist draft on every change
            self._draft_consent['signature'] = signature_var.get()
            self._draft_consent['ack_understood'] = ack_understood_var.get()
            self._draft_consent['ack_voluntary'] = ack_voluntary_var.get()
            valid = (
                bool(signature_var.get().strip())
                and ack_understood_var.get()
                and ack_voluntary_var.get()
            )
            confirm_btn.set_enabled(valid)

        signature_var.trace_add('write', on_change)

        ack_frame = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        ack_frame.pack(padx=40, pady=(10, 6), fill='x')
        tk.Checkbutton(
            ack_frame, text=s['consent_ack_understood'],
            variable=ack_understood_var, font=('Helvetica', 11),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
            activebackground=COLOR_BG_WINDOW,
            command=on_change, anchor='w',
        ).pack(anchor='w', pady=2)
        tk.Checkbutton(
            ack_frame, text=s['consent_ack_voluntary'],
            variable=ack_voluntary_var, font=('Helvetica', 11),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
            activebackground=COLOR_BG_WINDOW,
            command=on_change, anchor='w',
        ).pack(anchor='w', pady=2)

        # Confirm button
        def on_confirm():
            self.consent_reconfirmed_utc = now_utc_iso()
            self.consent_reconfirmed_language = self.language
            self.consent_signature = signature_var.get().strip()
            self.consent_text_shown = s['consent_text']
            # Session dir doesn't exist yet (created in _launch_session). Stage
            # the consent record to write on launch.
            self._pending_consent_record = {
                'consent_record_version': '1.0',
                'signed_utc': self.consent_reconfirmed_utc,
                'language': self.consent_reconfirmed_language,
                'participant_id': self.participant_id,
                'typed_signature': self.consent_signature,
                'acknowledged_understood': True,
                'acknowledged_voluntary': True,
                'consent_text': s['consent_text'],
            }
            # Demographics is now collected in-session by survey_prompter, so
            # always proceed to baseline.
            self._show_baseline()

        confirm_btn = PrimaryButton(self.root, s['consent_confirm'], on_confirm)
        confirm_btn.pack(pady=(14, 6))

        # Decline link
        decline_link = tk.Label(
            self.root, text=s['consent_decline'],
            font=('Helvetica', 11, 'underline'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
            cursor='hand2',
        )
        decline_link.pack(pady=(0, 18))

        def on_decline(_e):
            log_consent_decline(self.participant_id, self.language)
            self._show_consent_declined()

        decline_link.bind('<Button-1>', on_decline)
        decline_link.bind('<Enter>',
                          lambda _e: decline_link.config(fg=COLOR_TEXT_BODY))
        decline_link.bind('<Leave>',
                          lambda _e: decline_link.config(fg=COLOR_TEXT_MUTED))

        on_change()

    def _show_consent_declined(self):
        self.current_screen = 'consent_declined'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['consent_declined_title'], s['consent_declined_body'])

        close_btn = PrimaryButton(
            self.root, s['consent_declined_close'],
            lambda: self.root.destroy(),
        )
        close_btn.set_enabled(True)
        close_btn.pack(pady=(30, 20))

    # =========================================================================
    # Screen: Demographics
    # =========================================================================

    def _show_demographics(self):
        self.current_screen = 'demographics'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['demo_title'], s['demo_subtitle'])

        prev = self.demographics or {}
        form = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        form.pack(padx=40, pady=4, fill='x')

        # Use one StringVar per field with a "select" handler
        age_var       = tk.StringVar(value=prev.get('age_range', ''))
        gender_var    = tk.StringVar(value=prev.get('gender', ''))
        native_var    = tk.StringVar(value=prev.get('native_language', ''))
        exp_var       = tk.StringVar(value=prev.get('experience_years', ''))

        def on_change(*_a):
            valid = all([age_var.get(), gender_var.get(),
                         native_var.get(), exp_var.get()])
            continue_btn.set_enabled(valid)

        for var, label_key, options_key in [
            (age_var,    'demo_age',         'demo_age_options'),
            (gender_var, 'demo_gender',      'demo_gender_options'),
            (native_var, 'demo_native_lang', 'demo_native_lang_options'),
            (exp_var,    'demo_experience',  'demo_experience_options'),
        ]:
            self._add_section_label(form, s[label_key])
            group = OptionButtonGroup(form, var, s[options_key])
            group.pack(anchor='w', pady=(0, 4))
            var.trace_add('write', lambda *_a: on_change())

        # Navigation
        nav = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        nav.pack(pady=(28, 18))

        back_lbl = tk.Label(
            nav, text=s['back'], font=('Helvetica', 11, 'underline'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED, cursor='hand2',
        )
        back_lbl.pack(side='left', padx=20)
        back_lbl.bind('<Button-1>', lambda _e: self._show_splash())

        def on_continue():
            self.demographics = {
                'age_range': age_var.get(),
                'gender': gender_var.get(),
                'native_language': native_var.get(),
                'experience_years': exp_var.get(),
                'recorded_utc': now_utc_iso(),
                'recorded_language': self.language,
            }
            save_demographics(self.participant_id, self.demographics)
            self._show_baseline()

        continue_btn = PrimaryButton(nav, s['continue'], on_continue)
        continue_btn.frame.pack(side='left', padx=20)
        on_change()

    # =========================================================================
    # Screen: Baseline
    # =========================================================================

    def _show_baseline(self):
        self.current_screen = 'baseline'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['baseline_title'], s['baseline_subtitle'])

        form = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        form.pack(padx=40, pady=4, fill='x')

        # Sleep
        self._add_section_label(form, s['baseline_sleep'])
        sleep_var = tk.StringVar(value=self._draft_baseline.get('sleep', ''))
        sleep_entry = BorderedEntry(form, textvariable=sleep_var,
                                     font=('Helvetica', 13), width=8,
                                     justify='center', validate='numeric')
        sleep_entry.pack(anchor='w')

        # Caffeine
        self._add_section_label(form, s['baseline_caffeine'])
        caffeine_var = tk.StringVar(
            value=self._draft_baseline.get('caffeine', ''))
        caffeine_group = OptionButtonGroup(
            form, caffeine_var,
            options=s['baseline_caffeine_options'],
            display_labels=s['baseline_caffeine_labels'],
        )
        caffeine_group.pack(anchor='w', pady=(0, 4))
        caffeine_var.trace_add('write', lambda *_a: on_change())

        # Mood (Likert)
        self._add_section_label(form, s['baseline_mood'])
        mood_var = tk.IntVar(value=self._draft_baseline.get('mood', 0))
        LikertButtonGroup(form, mood_var).pack(anchor='w', pady=(0, 4))
        anchors_row = tk.Frame(form, bg=COLOR_BG_WINDOW)
        anchors_row.pack(fill='x', pady=(0, 4))
        tk.Label(anchors_row, text=s['baseline_mood_low'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED).pack(side='left', padx=(5, 0))
        tk.Label(anchors_row, text=s['baseline_mood_high'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED).pack(side='right', padx=(0, 5))

        # Energy (Likert)
        self._add_section_label(form, s['baseline_energy'])
        energy_var = tk.IntVar(value=self._draft_baseline.get('energy', 0))
        LikertButtonGroup(form, energy_var).pack(anchor='w', pady=(0, 4))
        anchors_row2 = tk.Frame(form, bg=COLOR_BG_WINDOW)
        anchors_row2.pack(fill='x', pady=(0, 4))
        tk.Label(anchors_row2, text=s['baseline_energy_low'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED).pack(side='left', padx=(5, 0))
        tk.Label(anchors_row2, text=s['baseline_energy_high'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED).pack(side='right', padx=(0, 5))

        # Discomfort
        self._add_section_label(form, s['baseline_discomfort'])
        discomfort_var = tk.StringVar(
            value=self._draft_baseline.get('discomfort', ''))
        discomfort_group = OptionButtonGroup(
            form, discomfort_var,
            options=s['baseline_discomfort_options'],
            display_labels=s['baseline_discomfort_labels'],
        )
        discomfort_group.pack(anchor='w', pady=(0, 6))
        discomfort_var.trace_add('write', lambda *_a: on_change())

        # Text field and hint label are created but NOT packed initially.
        # They appear only when the participant selects "Yes" (second option).
        discomfort_text_var = tk.StringVar(
            value=self._draft_baseline.get('discomfort_text', ''))
        discomfort_text = BorderedEntry(form, textvariable=discomfort_text_var,
                                          font=('Helvetica', 11))
        discomfort_hint = tk.Label(
            form, text=s['baseline_discomfort_text'],
            font=('Helvetica', 9, 'italic'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
        )
        discomfort_text_var.trace_add(
            'write',
            lambda *_a: self._draft_baseline.update(
                {'discomfort_text': discomfort_text_var.get()}))

        yes_option = s['baseline_discomfort_options'][1]  # canonical 'Yes'

        def update_discomfort_text_visibility(*_a):
            if discomfort_var.get() == yes_option:
                discomfort_text.pack(fill='x', pady=(4, 0))
                discomfort_hint.pack(anchor='w')
            else:
                discomfort_text.pack_forget()
                discomfort_hint.pack_forget()
                discomfort_text_var.set('')

        discomfort_var.trace_add('write', update_discomfort_text_visibility)
        # Apply initial visibility if discomfort was already "Yes" in draft
        if discomfort_var.get() == yes_option:
            discomfort_text.pack(fill='x', pady=(4, 0))
            discomfort_hint.pack(anchor='w')

        # Validation
        def all_filled():
            sleep_val = sleep_var.get().strip()
            try:
                sleep_float = float(sleep_val)
                if sleep_float < 0 or sleep_float > 24:
                    return False
            except ValueError:
                return False
            return (caffeine_var.get() and
                    mood_var.get() > 0 and
                    energy_var.get() > 0 and
                    discomfort_var.get())

        def on_change(*_a):
            # Persist draft on every change
            self._draft_baseline['sleep']      = sleep_var.get()
            self._draft_baseline['caffeine']   = caffeine_var.get()
            self._draft_baseline['mood']       = mood_var.get()
            self._draft_baseline['energy']     = energy_var.get()
            self._draft_baseline['discomfort'] = discomfort_var.get()
            continue_btn.set_enabled(all_filled())

        sleep_var.trace_add('write', on_change)
        mood_var.trace_add('write', on_change)
        energy_var.trace_add('write', on_change)

        # Navigation
        nav = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        nav.pack(pady=(22, 14))

        back_lbl = tk.Label(
            nav, text=s['back'], font=('Helvetica', 11, 'underline'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED, cursor='hand2',
        )
        back_lbl.pack(side='left', padx=20)
        back_lbl.bind('<Button-1>', lambda _e: self._show_consent())

        def on_continue():
            self.baseline = {
                'sleep_hours': float(sleep_var.get().strip()),
                'caffeine': caffeine_var.get(),
                'mood_likert_1_7': mood_var.get(),
                'energy_likert_1_7': energy_var.get(),
                'discomfort_present': discomfort_var.get(),
                'discomfort_description': discomfort_text.get().strip(),
                'recorded_utc': now_utc_iso(),
                'recorded_language': self.language,
            }
            self._show_equipment()

        continue_btn = PrimaryButton(nav, s['continue'], on_continue)
        continue_btn.frame.pack(side='left', padx=20)
        on_change()

    # =========================================================================
    # Screen: Equipment check
    # =========================================================================

    def _show_equipment(self):
        self.current_screen = 'equipment'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['equip_title'], s['equip_subtitle'])

        # Results area (rebuilt on each check pass)
        self.equip_results_frame = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        self.equip_results_frame.pack(padx=40, pady=10, fill='x')

        # Status banner (below results)
        self.equip_status_label = tk.Label(
            self.root, text='',
            font=('Helvetica', 13, 'italic'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
            wraplength=600, justify='center',
        )
        self.equip_status_label.pack(pady=(18, 8), padx=24)

        # Cancel link (only escape hatch; no other clicks needed)
        cancel_link = tk.Label(
            self.root, text=s['equip_cancel'],
            font=('Helvetica', 10, 'underline'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
            cursor='hand2',
        )
        cancel_link.pack(pady=(20, 14))
        cancel_link.bind('<Button-1>', lambda _e: self._show_baseline())
        cancel_link.bind('<Enter>',
                         lambda _e: cancel_link.config(fg=COLOR_TEXT_BODY))
        cancel_link.bind('<Leave>',
                         lambda _e: cancel_link.config(fg=COLOR_TEXT_MUTED))

        # Kick off first check round
        self._equip_results = {}  # last results, written to metadata
        self._equip_countdown_running = False
        self.root.after(200, self._run_equipment_checks)

    def _run_equipment_checks(self):
        if self.current_screen != 'equipment':
            return

        s = self._s()
        for widget in self.equip_results_frame.winfo_children():
            widget.destroy()

        all_passed = True
        self._equip_results = {}
        for key, fn in EQUIPMENT_CHECKS:
            passed, msg = fn()
            self._equip_results[key] = {'passed': passed, 'message': msg}
            if passed is False:
                all_passed = False

            # Pick icon + color
            if passed is True:
                icon, color, status_word = '✓', COLOR_BG_PRIMARY_ON, s['equip_status_pass']
            elif passed is False:
                icon, color, status_word = '✗', COLOR_BG_DANGER, s['equip_status_fail']
            else:
                icon, color, status_word = '!', COLOR_TEXT_MUTED, s['equip_status_unknown']

            row = tk.Frame(self.equip_results_frame, bg=COLOR_BG_WINDOW)
            row.pack(fill='x', pady=6)

            # Icon cell
            icon_cell = tk.Frame(row, width=36, height=36, bg=color,
                                  highlightthickness=0)
            icon_cell.pack_propagate(False)
            icon_cell.pack(side='left', padx=(0, 14))
            tk.Label(icon_cell, text=icon,
                     font=('Helvetica', 16, 'bold'),
                     bg=color, fg='#FFFFFF').pack(expand=True, fill='both')

            # Text column
            text_col = tk.Frame(row, bg=COLOR_BG_WINDOW)
            text_col.pack(side='left', fill='x', expand=True)
            tk.Label(text_col,
                     text=s['equip_check_labels'][key],
                     font=('Helvetica', 12, 'bold'),
                     bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
                     anchor='w').pack(fill='x')
            tk.Label(text_col, text=msg,
                     font=('Helvetica', 10),
                     bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
                     anchor='w', wraplength=480, justify='left').pack(fill='x')

        if all_passed:
            self.equip_status_label.config(
                text='', fg=COLOR_BG_PRIMARY_ON,
            )
            self._start_session_countdown(3)
        else:
            self._equip_countdown_running = False
            self.equip_status_label.config(
                text=s['equip_retry_msg'], fg=COLOR_BG_DANGER,
            )
            self._schedule_retry(5)

    def _schedule_retry(self, seconds):
        """Show countdown to next retry, then re-run checks."""
        if self.current_screen != 'equipment':
            return
        if seconds <= 0:
            self._run_equipment_checks()
            return
        s = self._s()
        existing = self.equip_status_label.cget('text')
        retry_msg = s['equip_retry_in'].format(n=seconds)
        self.equip_status_label.config(
            text=f"{s['equip_retry_msg']}\n{retry_msg}",
        )
        self.root.after(1000, lambda: self._schedule_retry(seconds - 1))

    def _start_session_countdown(self, n):
        """All checks passed: count down briefly then launch session."""
        if self.current_screen != 'equipment':
            return
        if n <= 0:
            # Persist check results into the in-memory equipment record
            self.equipment_checked = self._equip_results
            self._launch_session()
            self._show_running()
            return

        s = self._s()
        self.equip_status_label.config(
            text=s['equip_starting_in'].format(n=n),
            fg=COLOR_BG_PRIMARY_ON,
        )
        self.root.after(1000, lambda: self._start_session_countdown(n - 1))

    # =========================================================================
    # Session launch and subprocess management
    # =========================================================================

    def _launch_session(self):
        self.session_id = now_session_id(self.participant_id)
        self.session_dir = SESSIONS_DIR / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Determine whether demographics needs to be collected in-session
        # Demographics fires only for new participants. Source of truth is
        # the name->ID index lookup performed on the splash screen, NOT the
        # presence of a demographics.json file. This keeps the two consistent
        # and avoids surprising behavior if files are restored from backups,
        # manually moved, or deleted.
        needs_demographics = not self._participant_is_existing
        # Fire demographics ~2 min before timer ends, after the last Likert.
        # Two minutes is enough for the four categorical questions and
        # leaves a small buffer so the timer-end chime feels like a
        # natural close rather than a cutoff.
        demographics_at_min = max(1.0, (self.duration_sec / 60.0) - 2.0)

        # Write consent record file (legal evidence)
        if self._pending_consent_record:
            consent_path = (self.session_dir /
                            f'consent_{self.participant_id}_'
                            f'{self.session_id.split("_")[-1]}.txt')
            with open(consent_path, 'w', encoding='utf-8') as f:
                f.write('=' * 72 + '\n')
                f.write('DIGITAL CONSENT RECORD\n')
                f.write('=' * 72 + '\n')
                f.write(f"Participant ID:  {self._pending_consent_record['participant_id']}\n")
                f.write(f"Signed at (UTC): {self._pending_consent_record['signed_utc']}\n")
                f.write(f"Language:        {self._pending_consent_record['language']}\n")
                f.write(f"Typed signature: {self._pending_consent_record['typed_signature']}\n")
                f.write(f"Acknowledged understood:  yes\n")
                f.write(f"Acknowledged voluntary:   yes\n")
                f.write('=' * 72 + '\n')
                f.write('FULL CONSENT TEXT SHOWN TO PARTICIPANT\n')
                f.write('=' * 72 + '\n\n')
                f.write(self._pending_consent_record['consent_text'])
                f.write('\n')
            # Also save the structured record alongside
            consent_json_path = consent_path.with_suffix('.json')
            consent_json_path.write_text(
                json.dumps(self._pending_consent_record, indent=2,
                           ensure_ascii=False)
            )

        # Write session metadata
        metadata = {
            'session_id': self.session_id,
            'participant_id': self.participant_id,
            'session_dir': str(self.session_dir),
            'controller_language': self.language,
            'consent_signed_utc': self.consent_reconfirmed_utc,
            'consent_signed_language': self.consent_reconfirmed_language,
            'consent_typed_signature': self.consent_signature,
            'duration_planned_sec': self.duration_sec,
            'session_start_utc': now_utc_iso(),
            'demographics': self.demographics,
            'demographics_will_be_collected_in_session': needs_demographics,
            'demographics_scheduled_min': (demographics_at_min
                                           if needs_demographics else None),
            'baseline': self.baseline,
            'equipment_checks': self.equipment_checked,
            'dry_run': self.dry_run,
        }
        write_session_metadata(self.session_dir, metadata)

        self.session_start_t = time.time()

        # Initialize session-scoped state ONCE here. These persist across
        # any re-renders of the running screen (flag toggle etc).
        self._prompt_results = []
        self._fired_indices = set()
        self._modal_open = False
        self._poll_loop_running = False
        self._timer_end_sound_played = False

        # Compute the prompt schedule (Likert + optional demographics) using
        # the session start time. Prompts are fired in-process via
        # self.root.after() from _schedule_in_session_prompts.
        prompt_times_min = [float(x.strip())
                            for x in DEFAULT_PROMPT_TIMES.split(',')]
        rng = random.Random()  # fresh RNG; jitter is independent per session
        self._prompt_schedule = compute_prompt_schedule(
            session_start_t=self.session_start_t,
            prompt_times_min=prompt_times_min,
            jitter_sec=DEFAULT_JITTER_SEC,
            demographics_at_min=(demographics_at_min if needs_demographics
                                 else None),
            rng=rng,
        )
        # Schedule all prompts NOW (not on every re-render of the running
        # screen). This is critical to prevent duplicate firings.
        self._schedule_in_session_prompts()

        # Start the poll loop ONCE. It self-perpetuates via root.after()
        # until the session ends. Guard prevents accidental double-start.
        if not self._poll_loop_running:
            self._poll_loop_running = True
            self.root.after(SUBPROCESS_POLL_MS, self._poll_tick)
        # Write session_start record to survey.jsonl
        append_survey_jsonl(self.session_dir, {
            'type': 'session_start',
            'timestamp_utc': now_utc_iso(),
            'session_duration_sec': self.duration_sec,
            'prompt_times_nominal_min': prompt_times_min,
            'jitter_sec': DEFAULT_JITTER_SEC,
            'initial_language': self.language,
            'participant_id': self.participant_id,
            'demographics_scheduled': needs_demographics,
            'prompts_scheduled': [
                {
                    'nominal_min': item['nominal_min'],
                    'actual_offset_sec': round(item['actual_offset_sec'], 2),
                    'scheduled_utc': datetime.fromtimestamp(
                        item['fire_ts'], timezone.utc).isoformat(),
                    'prompt_type': item['prompt_type'],
                }
                for item in self._prompt_schedule
            ],
        })

        # Spawn the acquisition subprocess. survey_prompter is no longer
        # spawned; the Likert and demographics prompts now run in this
        # process as Toplevel modals.
        self.survey_proc = None  # legacy field, kept for _poll_tick safety
        if self.dry_run:
            self.session_proc = subprocess.Popen(
                ['sleep', str(self.duration_sec)],
            )
        else:
            env = {**os.environ, 'SESSION_DIR': str(self.session_dir)}
            self.session_proc = subprocess.Popen(
                ['bash', str(START_SESSION_SH),
                 str(self.duration_sec), self.participant_id],
                env=env,
            )

        # Spawn the floating task checklist widget. Reads session_start_utc
        # from session_metadata.json so its countdown stays in sync.
        # Fails silently if task_checklist.py is missing — the session can
        # run without the widget, the participant just won't see it.
        if TASK_CHECKLIST.exists():
            try:
                self._checklist_proc = subprocess.Popen(
                    [sys.executable, str(TASK_CHECKLIST),
                     str(self.session_dir),
                     '--duration', str(self.duration_sec),
                     '--language', self.language],
                )
            except Exception as e:
                print(f'WARN: could not spawn task_checklist: {e}',
                      file=sys.stderr)
                self._checklist_proc = None
        else:
            print(f'NOTE: {TASK_CHECKLIST} not found; running without widget',
                  file=sys.stderr)

    def _terminate_subprocesses(self, why='abort'):
        """Terminate the acquisition subprocess and the task checklist widget.
        survey_prompter no longer exists as a subprocess; prompts run
        in-process."""
        # Cancel any pending after()-scheduled prompts so they don't fire
        # after the session has been aborted.
        self._cancel_pending_prompts()
        # Terminate the task checklist widget if running
        self._terminate_checklist()
        if self.session_proc is None:
            return
        if self.session_proc.poll() is None:
            try:
                self.session_proc.terminate()
            except Exception:
                pass
        # Wait for the cleanup trap (recording move + osquery slice) to run.
        # The recording can be hundreds of MB, so the move alone takes a few
        # seconds; the osquery slice reads the whole global log. Give it up to
        # CLEANUP_GRACE_SEC before resorting to SIGKILL, so that even an
        # aborted session keeps as much data as possible.
        deadline = time.time() + CLEANUP_GRACE_SEC
        while self.session_proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if self.session_proc.poll() is None:
            try:
                self.session_proc.kill()
            except Exception:
                pass
        self.session_exit_code = self.session_proc.poll()

    def _terminate_checklist(self):
        """Terminate the task checklist widget subprocess if alive.
        Idempotent: safe to call multiple times."""
        if self._checklist_proc is None:
            return
        if self._checklist_proc.poll() is None:
            try:
                self._checklist_proc.terminate()
            except Exception:
                pass
        # Brief wait, then SIGKILL if still alive
        deadline = time.time() + 2.0
        while self._checklist_proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        if self._checklist_proc.poll() is None:
            try:
                self._checklist_proc.kill()
            except Exception:
                pass
        self._checklist_proc = None

    # =========================================================================
    # Screen: Running
    # =========================================================================

    def _show_running(self):
        self.current_screen = 'running'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['running_title'], s['running_subtitle'])

        # Big countdown timer
        self.countdown_label = tk.Label(
            self.root, text='40:00',
            font=('Helvetica', 56, 'bold'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_TITLE,
        )
        self.countdown_label.pack(pady=(20, 12))

        # Progress bar (Canvas-based for color control)
        self.progress_canvas = tk.Canvas(
            self.root, width=540, height=14,
            bg=COLOR_BG_PROGRESS, highlightthickness=0,
        )
        self.progress_canvas.pack(pady=(0, 24))
        self.progress_fill = self.progress_canvas.create_rectangle(
            0, 0, 0, 14, fill=COLOR_FG_PROGRESS, width=0,
        )

        # Streams status
        self.streams_status_label = tk.Label(
            self.root, text=f"{s['running_streams']}: {s['running_streams_ok']}",
            font=('Helvetica', 12),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
        )
        self.streams_status_label.pack(pady=(0, 8))

        # Session dir display
        tk.Label(
            self.root, text=str(self.session_dir),
            font=('Helvetica', 10, 'italic'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
        ).pack(pady=(0, 30))

        # Abort button (danger style)
        def on_abort():
            from tkinter import messagebox
            if messagebox.askyesno(
                s['running_abort_confirm_title'],
                s['running_abort_confirm_msg'],
            ):
                self._terminate_subprocesses(why='user_abort')
                self._show_complete(aborted=True)

        abort_frame = tk.Frame(self.root, bg=COLOR_BG_DANGER,
                                highlightthickness=0, cursor='hand2')
        abort_label = tk.Label(
            abort_frame, text=s['running_abort'],
            font=('Helvetica', 12, 'bold'),
            bg=COLOR_BG_DANGER, fg=COLOR_FG_DANGER,
            padx=28, pady=10, cursor='hand2',
        )
        abort_label.pack()
        abort_frame.pack(pady=(10, 20))
        abort_frame.bind('<Button-1>', lambda _e: on_abort())
        abort_label.bind('<Button-1>', lambda _e: on_abort())
        for w in (abort_frame, abort_label):
            w.bind('<Enter>', lambda _e:
                   (abort_frame.config(bg=COLOR_BG_DANGER_HOT),
                    abort_label.config(bg=COLOR_BG_DANGER_HOT)))
            w.bind('<Leave>', lambda _e:
                   (abort_frame.config(bg=COLOR_BG_DANGER),
                    abort_label.config(bg=COLOR_BG_DANGER)))

        # NOTE: scheduling prompts and starting the poll loop happen ONCE
        # in _launch_session, not here. Re-rendering this screen (e.g.
        # on language toggle) must not re-schedule anything.

    def _poll_tick(self):
        if self.current_screen != 'running':
            return
        # Widgets are built in _show_running. If they're not present yet
        # (e.g. extremely fast first tick before render), skip and reschedule.
        if not hasattr(self, 'countdown_label'):
            self.root.after(SUBPROCESS_POLL_MS, self._poll_tick)
            return

        elapsed = time.time() - self.session_start_t
        remaining = max(0, self.duration_sec - elapsed)
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        try:
            self.countdown_label.config(text=f'{mins:02d}:{secs:02d}')
        except tk.TclError:
            # Widget was destroyed between checks (likely a screen transition).
            # Reschedule and let the next tick figure it out.
            self.root.after(SUBPROCESS_POLL_MS, self._poll_tick)
            return

        # Progress bar
        pct = min(1.0, elapsed / self.duration_sec)
        try:
            self.progress_canvas.coords(self.progress_fill, 0, 0, int(540 * pct), 14)
        except tk.TclError:
            pass

        s = self._s()
        session_alive = (self.session_proc is not None
                         and self.session_proc.poll() is None)
        modal_open = getattr(self, '_modal_open', False)

        if remaining > 0:
            # Active timer window
            try:
                if session_alive:
                    self.streams_status_label.config(
                        text=f"{s['running_streams']}: {s['running_streams_ok']}",
                        fg=COLOR_TEXT_BODY,
                    )
                else:
                    self.streams_status_label.config(
                        text=f"{s['running_streams']}: {s['running_streams_warn']}",
                        fg=COLOR_BG_DANGER,
                    )
            except tk.TclError:
                pass
            self.root.after(SUBPROCESS_POLL_MS, self._poll_tick)
            return

        # ---- Timer expired ----
        # Play timer-end sound once at the moment we cross zero.
        if not self._timer_end_sound_played:
            self._timer_end_sound_played = True
            play_timer_end_sound()

        # Send SIGTERM so start_session.sh's cleanup trap fires. The trap
        # finalizes the OBS recording (moves recording.mp4 into the session
        # dir) and slices osquery events into osquery.jsonl. These run
        # synchronously inside the trap, so we MUST wait for the process to
        # exit rather than polling once and moving on - otherwise both files
        # are lost even on a clean completion.
        if session_alive:
            try:
                self.session_proc.terminate()
            except Exception:
                pass

        # If a prompt modal is open (typically the demographics popup, which
        # fires near the end of the session), wait for it to close before
        # transitioning to complete. The participant must finish on their
        # own terms; we never kill an open modal.
        if modal_open:
            try:
                self.countdown_label.config(text='00:00')
                self.streams_status_label.config(
                    text=s['running_waiting_for_survey'],
                    fg=COLOR_TEXT_MUTED,
                )
            except tk.TclError:
                pass
            self.root.after(SUBPROCESS_POLL_MS, self._poll_tick)
            return

        # Wait for start_session.sh to finish its cleanup trap (recording
        # move + osquery slice). Give it up to CLEANUP_GRACE_SEC.
        #
        # Previously this was a single blocking session_proc.wait(timeout=...)
        # call on the Tk main thread, which froze the entire UI for up to 60s —
        # P09's operator saw this as a "loop trying to close OBS" and Ctrl-C'd.
        # Now we poll on after() so the event loop stays alive, the countdown
        # shows an advancing "Finalizing (Ns)..." indicator, and the operator
        # can see that work is actually happening.
        self._finalize_start_t = time.time()
        self._poll_finalize()

    def _poll_finalize(self):
        """Non-blocking poll during cleanup-trap wait. Called on after() cadence.
        Advances an elapsed counter in the UI and transitions to debrief once
        session_proc exits or CLEANUP_GRACE_SEC is exceeded."""
        s = self._s()
        elapsed = int(time.time() - self._finalize_start_t)
        proc_done = (self.session_proc is None
                     or self.session_proc.poll() is not None)
        timed_out = elapsed >= CLEANUP_GRACE_SEC

        if not proc_done and not timed_out:
            # Still waiting — update the visible counter and reschedule.
            try:
                self.countdown_label.config(text='00:00')
                self.streams_status_label.config(
                    text=(f"{s.get('running_finalizing', 'Finalizing session data...')}"
                          f" ({elapsed}s)"),
                    fg=COLOR_TEXT_MUTED,
                )
            except tk.TclError:
                pass
            self.root.after(SUBPROCESS_POLL_MS, self._poll_finalize)
            return

        # Reached here: process exited or deadline hit.
        if timed_out and not proc_done:
            # Cleanup took too long. Kill it; recording may be orphaned and
            # osquery un-sliced. The Complete screen's validation will flag
            # this; reslice_osquery.py can recover both files after the fact.
            try:
                self.session_proc.kill()
            except Exception:
                pass

        self.session_exit_code = (self.session_proc.poll()
                                   if self.session_proc else None)
        # Close the floating task checklist widget before the debrief opens
        # so the participant isn't looking at two timer-related windows.
        self._terminate_checklist()
        self._show_debrief(aborted=False)

    # =========================================================================
    # Screen: Complete
    # =========================================================================

    # =========================================================================
    # In-session prompt modals (Likert and Demographics)
    # =========================================================================
    #
    # These run as tk.Toplevel windows on top of the main controller window.
    # The countdown timer in the running screen keeps ticking because the
    # main event loop continues to process self.root.after() callbacks while
    # wait_window blocks. Single-process, single-Tk-root design.

    def _schedule_in_session_prompts(self):
        """Schedule all prompt firings via self.root.after().
        Called ONCE per session, from _launch_session. State variables
        (_prompt_results, _fired_indices, _modal_open) are initialized in
        _launch_session, not here, so this is safe to be called only once."""
        self._pending_after_ids = []

        for idx, item in enumerate(self._prompt_schedule):
            delay_sec = item['fire_ts'] - time.time()
            if delay_sec < 0:
                delay_sec = 0
            delay_ms = int(delay_sec * 1000)
            after_id = self.root.after(
                delay_ms,
                lambda i=idx: self._fire_prompt(i),
            )
            self._pending_after_ids.append(after_id)

    def _cancel_pending_prompts(self):
        """Cancel any scheduled prompts that haven't fired yet."""
        for aid in getattr(self, '_pending_after_ids', []):
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        self._pending_after_ids = []

    def _fire_prompt(self, idx):
        """Callback invoked by after() when a scheduled prompt fires.
        Idempotent: if the same idx fires twice (e.g. due to a re-render
        accidentally re-scheduling), the second call is ignored."""
        # Only fire if we're still on the running screen
        if self.current_screen != 'running':
            return
        # Idempotency guard: don't double-fire the same prompt
        if idx in self._fired_indices:
            return
        self._fired_indices.add(idx)

        item = self._prompt_schedule[idx]
        scheduled_utc = datetime.fromtimestamp(
            item['fire_ts'], timezone.utc).isoformat()

        self._modal_open = True
        try:
            if item['prompt_type'] == 'likert':
                result = self._run_likert_modal()
            elif item['prompt_type'] == 'demographics':
                result = self._run_demographics_modal()
            else:
                return
        except Exception as exc:
            # Modal construction raised (e.g. mismatched options/labels list,
            # missing STRINGS key). A blank window with no handlers would otherwise
            # block teardown indefinitely or silently drop the survey record.
            # Log the error, write an error record so the survey JSONL has a trace,
            # and return cleanly so the session can proceed to debrief.
            import traceback
            print(f'[_fire_prompt] modal raised for idx={idx}: {exc}', file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            shown_utc = now_utc_iso()
            error_result = {
                'shown_utc':           shown_utc,
                'response_utc':        shown_utc,
                'response_latency_ms': None,
                'outcome':             'error',
                'responses':           {},
                'language':            self.language,
                'error':               repr(exc),
            }
            record = {
                'type': 'prompt',
                'prompt_type': item['prompt_type'],
                'prompt_index': idx,
                'nominal_min': item['nominal_min'],
                'actual_offset_sec': round(item['actual_offset_sec'], 2),
                'prompt_scheduled_utc': scheduled_utc,
                **{k: error_result[k] for k in
                   ('shown_utc', 'response_utc', 'response_latency_ms',
                    'outcome', 'responses', 'language', 'error')},
            }
            append_survey_jsonl(self.session_dir, record)
            return
        finally:
            self._modal_open = False

        # Build the JSONL record
        record = {
            'type': 'prompt',
            'prompt_type': item['prompt_type'],
            'prompt_index': idx,
            'nominal_min': item['nominal_min'],
            'actual_offset_sec': round(item['actual_offset_sec'], 2),
            'prompt_scheduled_utc': scheduled_utc,
            'prompt_shown_utc': result['shown_utc'],
            'response_utc': result['response_utc'],
            'response_latency_ms': result['response_latency_ms'],
            'outcome': result['outcome'],
            'responses': result['responses'],
            'language': result['language'],
        }
        if item['prompt_type'] == 'likert':
            record['first_interaction_utc'] = result.get('first_interaction_utc')
            record['noticing_latency_ms'] = result.get('noticing_latency_ms')
        append_survey_jsonl(self.session_dir, record)
        self._prompt_results.append(record)

        # Demographics: persist to participant directory
        if (item['prompt_type'] == 'demographics'
                and result['outcome'] == 'submitted'):
            demo_dir = PARTICIPANTS_DIR / self.participant_id
            demo_dir.mkdir(parents=True, exist_ok=True)
            demo_record = {
                **result['responses'],
                'recorded_utc': result['response_utc'],
                'recorded_language': result['language'],
                'session_id': self.session_id,
                'collected_via': 'session_controller_modal',
            }
            (demo_dir / 'demographics.json').write_text(
                json.dumps(demo_record, indent=2, ensure_ascii=False)
            )

    def _center_modal(self, modal, min_w=620, min_h=560):
        """Center a Toplevel modal on screen."""
        modal.update_idletasks()
        ww = max(modal.winfo_reqwidth(), min_w)
        wh = max(modal.winfo_reqheight(), min_h)
        sw = modal.winfo_screenwidth()
        sh = modal.winfo_screenheight()
        modal.geometry(f'{ww}x{wh}+{(sw - ww) // 2}+{(sh - wh) // 2}')

    def _add_modal_flag_toggle(self, modal, on_change_lang, on_reload=None):
        """Top-right EN/PT flag toggle for a modal Toplevel.

        on_reload: optional callback. If provided, a '↻ Reload' button is placed
        on the left of the same bar. Clicking it forces a full window repaint
        (withdraw/deiconify) to recover a blank render. If the form is already
        complete the callback should submit and close instead.
        """
        bar = tk.Frame(modal, bg=COLOR_BG_WINDOW)
        bar.pack(fill='x', padx=20, pady=(10, 0))

        if on_reload is not None:
            reload_lbl = tk.Label(
                bar, text='↻ Reload',
                font=('Helvetica', 10),
                bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED, cursor='hand2',
            )
            reload_lbl.pack(side='left', padx=4)
            reload_lbl.bind('<Button-1>', lambda _e: on_reload())
            reload_lbl.bind('<Enter>',
                            lambda _e: reload_lbl.config(fg=COLOR_TEXT_BODY))
            reload_lbl.bind('<Leave>',
                            lambda _e: reload_lbl.config(fg=COLOR_TEXT_MUTED))

        flag_labels = {}
        for lang in ('pt', 'en'):
            lbl = tk.Label(
                bar, text=FLAGS[lang],
                font=('Helvetica',
                      FLAG_FONT_SELECTED if lang == self.language
                      else FLAG_FONT_UNSELECTED),
                bg=COLOR_BG_WINDOW, cursor='hand2',
            )
            lbl.pack(side='right', padx=4)
            lbl.bind('<Button-1>', lambda _e, l=lang: on_change_lang(l))
            flag_labels[lang] = lbl
        return flag_labels

    # -------------------------------------------------------------------------
    # Likert modal
    # -------------------------------------------------------------------------

    def _run_likert_modal(self):
        """Show the three-question Likert modal. Returns result dict."""
        s = self._s()

        # Pre-construction validation: raise before creating any widget so a bad
        # STRINGS config produces a clean error record, not a ghost window.
        _required_likert_keys = [
            'likert_window_title', 'likert_title', 'likert_context',
            'likert_questions', 'likert_anchors', 'likert_submit', 'likert_skip',
        ]
        for k in _required_likert_keys:
            if k not in s:
                raise KeyError(f'STRINGS[{self.language!r}] is missing key {k!r}')
        for qkey in ('focus', 'frustration', 'effort'):
            if qkey not in s.get('likert_questions', {}):
                raise KeyError(f'STRINGS[{self.language!r}][likert_questions] missing {qkey!r}')
            if qkey not in s.get('likert_anchors', {}):
                raise KeyError(f'STRINGS[{self.language!r}][likert_anchors] missing {qkey!r}')

        state = {
            'outcome': 'dismissed',
            'responses': {},
            'first_interaction_utc': None,
            'first_interaction_t': None,
            'response_utc': None,
            'response_t': None,
            'language': self.language,
        }

        modal = tk.Toplevel(self.root)
        modal.title(s['likert_window_title'])
        modal.configure(bg=COLOR_BG_WINDOW)
        modal.transient(self.root)
        modal.grab_set()
        modal.attributes('-topmost', True)

        refs = {
            'title_label': None,
            'context_label': None,
            'question_labels': {},
            'anchor_low_labels': {},
            'anchor_high_labels': {},
            'submit_label': None,
            'skip_label': None,
        }
        likert_vars = {}
        question_keys = ('focus', 'frustration', 'effort')

        def all_answered():
            return all(likert_vars[k].get() > 0 for k in question_keys)

        def on_change(*_a):
            if state['first_interaction_utc'] is None:
                state['first_interaction_utc'] = now_utc_iso()
                state['first_interaction_t'] = time.time()
            _refresh_submit_visual()

        def _refresh_submit_visual():
            if all_answered():
                submit_frame.config(bg=COLOR_BG_PRIMARY_ON, cursor='hand2')
                refs['submit_label'].config(bg=COLOR_BG_PRIMARY_ON,
                                             fg=COLOR_FG_PRIMARY_ON,
                                             cursor='hand2')
            else:
                submit_frame.config(bg=COLOR_BG_PRIMARY_OFF, cursor='arrow')
                refs['submit_label'].config(bg=COLOR_BG_PRIMARY_OFF,
                                             fg=COLOR_FG_PRIMARY_OFF,
                                             cursor='arrow')

        def on_submit():
            if not all_answered():
                return
            state['outcome'] = 'submitted'
            state['responses'] = {k: likert_vars[k].get() for k in question_keys}
            state['response_utc'] = now_utc_iso()
            state['response_t'] = time.time()
            modal.destroy()

        def on_skip():
            state['outcome'] = 'skipped'
            state['response_utc'] = now_utc_iso()
            state['response_t'] = time.time()
            modal.destroy()

        def on_close():
            # Deliberately a no-op. The window-close (red X) and the Escape key
            # must NOT silently dismiss a check-in - in an early pilot a focused
            # participant reflexively closed two of three prompts, costing two
            # of three within-participant affect measurements. The only ways
            # out of this modal are answering all three questions and clicking
            # Submit, or clicking the explicit "Skip" button. Both are
            # deliberate and both are logged with a clear outcome. Withdrawing
            # from the study entirely remains available on the main window.
            pass

        # Intercept the window-close button and the Escape key so neither can
        # destroy the modal. grab_set() above already keeps focus on the modal.
        modal.protocol('WM_DELETE_WINDOW', on_close)
        modal.bind('<Escape>', lambda _e: 'break')

        def apply_language(lang):
            if lang not in SUPPORTED_LANGUAGES or lang == state['language']:
                return
            state['language'] = lang
            self.language = lang  # persist to controller
            ls = STRINGS[lang]
            modal.title(ls['likert_window_title'])
            refs['title_label'].config(text=ls['likert_title'])
            refs['context_label'].config(text=ls['likert_context'])
            for k in question_keys:
                refs['question_labels'][k].config(text=ls['likert_questions'][k])
                lo, hi = ls['likert_anchors'][k]
                refs['anchor_low_labels'][k].config(text=lo)
                refs['anchor_high_labels'][k].config(text=hi)
            refs['submit_label'].config(text=ls['likert_submit'])
            refs['skip_label'].config(text=ls['likert_skip'])
            for lk, lbl in flag_labels.items():
                lbl.config(font=('Helvetica',
                                 FLAG_FONT_SELECTED if lk == lang
                                 else FLAG_FONT_UNSELECTED))

        def on_reload():
            # If all three questions are already answered, treat reload as a
            # submit — the participant is done and the window just looks stuck.
            if all_answered():
                on_submit()
                return
            # Otherwise force a full OS-level repaint: withdraw unmaps the
            # window, deiconify remaps it, and update() flushes the draw queue.
            # This recovers a blank render without losing any entered responses.
            modal.withdraw()
            modal.update_idletasks()
            modal.deiconify()
            modal.update()
            modal.lift()
            modal.focus_force()

        flag_labels = self._add_modal_flag_toggle(modal, apply_language,
                                                   on_reload=on_reload)

        refs['title_label'] = tk.Label(
            modal, text=s['likert_title'],
            font=('Helvetica', 20, 'bold'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_TITLE,
        )
        refs['title_label'].pack(pady=(4, 6), padx=24)

        refs['context_label'] = tk.Label(
            modal, text=s['likert_context'],
            font=('Helvetica', 11),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
            wraplength=560, justify='center',
        )
        refs['context_label'].pack(pady=(0, 16), padx=24)

        sep = tk.Frame(modal, height=1, bg='#E0E0E0')
        sep.pack(fill='x', padx=24, pady=(0, 12))

        for key in question_keys:
            block = tk.Frame(modal, bg=COLOR_BG_WINDOW)
            block.pack(padx=24, pady=(8, 4))
            q_label = tk.Label(
                block, text=s['likert_questions'][key],
                font=('Helvetica', 18, 'bold'),
                bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
            )
            q_label.pack(anchor='w', pady=(0, 8))
            refs['question_labels'][key] = q_label

            var = tk.IntVar(value=0)
            var.trace_add('write', on_change)
            likert_vars[key] = var

            group = LikertButtonGroup(block, var, size=52, font_size=18)
            group.pack(anchor='w', pady=(0, 4))

            anchor_low, anchor_high = s['likert_anchors'][key]
            anchor_row = tk.Frame(block, bg=COLOR_BG_WINDOW)
            anchor_row.pack(fill='x', pady=(2, 6))
            lo_lbl = tk.Label(
                anchor_row, text=anchor_low,
                font=('Helvetica', 9, 'italic'),
                bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
            )
            lo_lbl.pack(side='left', padx=(5, 0))
            hi_lbl = tk.Label(
                anchor_row, text=anchor_high,
                font=('Helvetica', 9, 'italic'),
                bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
            )
            hi_lbl.pack(side='right', padx=(0, 5))
            refs['anchor_low_labels'][key] = lo_lbl
            refs['anchor_high_labels'][key] = hi_lbl

        # Submit (Frame+Label so macOS honors the green)
        submit_frame = tk.Frame(
            modal, bg=COLOR_BG_PRIMARY_OFF, highlightthickness=0, cursor='arrow',
        )
        refs['submit_label'] = tk.Label(
            submit_frame, text=s['likert_submit'],
            font=('Helvetica', 14, 'bold'),
            bg=COLOR_BG_PRIMARY_OFF, fg=COLOR_FG_PRIMARY_OFF,
            padx=44, pady=12, cursor='arrow',
        )
        refs['submit_label'].pack()
        submit_frame.pack(pady=(14, 8))

        def _submit_click(_e): on_submit()
        submit_frame.bind('<Button-1>', _submit_click)
        refs['submit_label'].bind('<Button-1>', _submit_click)

        def _hover_in(_e):
            if all_answered():
                submit_frame.config(bg=COLOR_BG_PRIMARY_HOT)
                refs['submit_label'].config(bg=COLOR_BG_PRIMARY_HOT)

        def _hover_out(_e):
            if all_answered():
                submit_frame.config(bg=COLOR_BG_PRIMARY_ON)
                refs['submit_label'].config(bg=COLOR_BG_PRIMARY_ON)

        for w in (submit_frame, refs['submit_label']):
            w.bind('<Enter>', _hover_in)
            w.bind('<Leave>', _hover_out)

        refs['skip_label'] = tk.Label(
            modal, text=s['likert_skip'],
            font=('Helvetica', 10, 'underline'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED, cursor='hand2',
        )
        refs['skip_label'].pack(pady=(0, 22))
        refs['skip_label'].bind('<Button-1>', lambda _e: on_skip())

        modal.bind('<Return>', lambda _e: on_submit())

        self._center_modal(modal, min_w=620, min_h=660)
        # update_idletasks() (inside _center_modal) calculates geometry but does
        # not flush the draw queue. On macOS the content area renders blank until
        # the event loop gets a full redraw pass. modal.update() forces that pass
        # before wait_window() takes over, so the participant sees the questions.
        modal.update()
        modal.lift()
        modal.focus_force()

        shown_utc = now_utc_iso()
        shown_t = time.time()

        # Block here, but the main Tk event loop keeps spinning (countdown
        # continues to tick in the background)
        self.root.wait_window(modal)

        response_latency_ms = (
            int((state['response_t'] - shown_t) * 1000)
            if state['response_t'] is not None else None
        )
        noticing_latency_ms = (
            int((state['first_interaction_t'] - shown_t) * 1000)
            if state['first_interaction_t'] is not None else None
        )

        return {
            'shown_utc':             shown_utc,
            'first_interaction_utc': state['first_interaction_utc'],
            'response_utc':          state['response_utc'],
            'noticing_latency_ms':   noticing_latency_ms,
            'response_latency_ms':   response_latency_ms,
            'outcome':               state['outcome'],
            'responses':             state['responses'],
            'language':              state['language'],
        }

    # -------------------------------------------------------------------------
    # Demographics modal
    # -------------------------------------------------------------------------

    def _run_demographics_modal(self):
        """Show the four-question demographics modal. Returns result dict."""
        s = self._s()

        # Pre-construction validation: verify every key the builder reads actually
        # exists in STRINGS and that every options/labels pair has matching lengths.
        # Do this BEFORE creating the Toplevel so a bad config raises clean (no
        # ghost window). _fire_prompt's outer catch will write an error record.
        _required_pairs = [
            ('demo_age_options',         'demo_age_labels'),
            ('demo_gender_options',      'demo_gender_labels'),
            ('demo_native_lang_options', 'demo_native_lang_labels'),
            ('demo_experience_options',  'demo_experience_labels'),
        ]
        _required_keys = [
            'demo_modal_window_title', 'demo_modal_title', 'demo_modal_context',
            'demo_modal_submit',
            'demo_age', 'demo_gender', 'demo_native_lang', 'demo_experience',
        ]
        for k in _required_keys:
            if k not in s:
                raise KeyError(f'STRINGS[{self.language!r}] is missing key {k!r}')
        for opts_key, labels_key in _required_pairs:
            if opts_key not in s:
                raise KeyError(f'STRINGS[{self.language!r}] is missing key {opts_key!r}')
            if labels_key not in s:
                raise KeyError(f'STRINGS[{self.language!r}] is missing key {labels_key!r}')
            if len(s[opts_key]) != len(s[labels_key]):
                raise ValueError(
                    f'STRINGS[{self.language!r}]: {opts_key!r} has {len(s[opts_key])} items '
                    f'but {labels_key!r} has {len(s[labels_key])} — must match'
                )

        state = {
            'outcome': 'dismissed',
            'responses': {},
            'response_utc': None,
            'response_t': None,
            'language': self.language,
        }

        modal = tk.Toplevel(self.root)
        modal.title(s['demo_modal_window_title'])
        modal.configure(bg=COLOR_BG_WINDOW)
        modal.transient(self.root)
        modal.grab_set()
        modal.attributes('-topmost', True)

        age_var       = tk.StringVar(value='')
        gender_var    = tk.StringVar(value='')
        native_var    = tk.StringVar(value='')
        exp_var       = tk.StringVar(value='')

        refs = {
            'title_label': None,
            'context_label': None,
            'question_labels': {},
            'submit_label': None,
        }
        groups = {}  # key -> OptionButtonGroup, for later set_display_labels

        def all_answered():
            return all([age_var.get(), gender_var.get(),
                        native_var.get(), exp_var.get()])

        def _refresh_submit_visual():
            if all_answered():
                submit_frame.config(bg=COLOR_BG_PRIMARY_ON, cursor='hand2')
                refs['submit_label'].config(bg=COLOR_BG_PRIMARY_ON,
                                             fg=COLOR_FG_PRIMARY_ON,
                                             cursor='hand2')
            else:
                submit_frame.config(bg=COLOR_BG_PRIMARY_OFF, cursor='arrow')
                refs['submit_label'].config(bg=COLOR_BG_PRIMARY_OFF,
                                             fg=COLOR_FG_PRIMARY_OFF,
                                             cursor='arrow')

        for v in (age_var, gender_var, native_var, exp_var):
            v.trace_add('write', lambda *_a: _refresh_submit_visual())

        def on_submit():
            if not all_answered():
                return
            state['outcome'] = 'submitted'
            state['responses'] = {
                'age_range':         age_var.get(),
                'gender':            gender_var.get(),
                'native_language':   native_var.get(),
                'experience_years':  exp_var.get(),
            }
            state['response_utc'] = now_utc_iso()
            state['response_t'] = time.time()
            modal.destroy()

        def on_close():
            # Deliberately a no-op, matching the Likert modal (invariant 7).
            # The red X and Escape must not silently dismiss the demographics
            # prompt — P07 lost two Likert measurements this way, and P09
            # triggered a blank-window/teardown-hang when the modal was left
            # in an inconsistent state. The only exits are Submit or Skip.
            pass

        modal.protocol('WM_DELETE_WINDOW', on_close)
        modal.bind('<Escape>', lambda _e: 'break')

        def apply_language(lang):
            if lang not in SUPPORTED_LANGUAGES or lang == state['language']:
                return
            state['language'] = lang
            self.language = lang
            ls = STRINGS[lang]
            modal.title(ls['demo_modal_window_title'])
            refs['title_label'].config(text=ls['demo_modal_title'])
            refs['context_label'].config(text=ls['demo_modal_context'])
            refs['question_labels']['age'].config(text=ls['demo_age'])
            refs['question_labels']['gender'].config(text=ls['demo_gender'])
            refs['question_labels']['native'].config(text=ls['demo_native_lang'])
            refs['question_labels']['experience'].config(text=ls['demo_experience'])
            refs['submit_label'].config(text=ls['demo_modal_submit'])
            # Update the option button display labels in place, preserving
            # the participant's current selection
            groups['age'].set_display_labels(ls['demo_age_labels'])
            groups['gender'].set_display_labels(ls['demo_gender_labels'])
            groups['native'].set_display_labels(ls['demo_native_lang_labels'])
            groups['experience'].set_display_labels(ls['demo_experience_labels'])
            for lk, lbl in flag_labels.items():
                lbl.config(font=('Helvetica',
                                 FLAG_FONT_SELECTED if lk == lang
                                 else FLAG_FONT_UNSELECTED))

        def on_reload():
            # Same recovery as the Likert modal: submit if complete, otherwise
            # force a full OS repaint to recover a blank render.
            if all_answered():
                on_submit()
                return
            modal.withdraw()
            modal.update_idletasks()
            modal.deiconify()
            modal.update()
            modal.lift()
            modal.focus_force()

        flag_labels = self._add_modal_flag_toggle(modal, apply_language,
                                                   on_reload=on_reload)

        refs['title_label'] = tk.Label(
            modal, text=s['demo_modal_title'],
            font=('Helvetica', 20, 'bold'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_TITLE,
        )
        refs['title_label'].pack(pady=(4, 6), padx=24)

        refs['context_label'] = tk.Label(
            modal, text=s['demo_modal_context'],
            font=('Helvetica', 11),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
            wraplength=560, justify='center',
        )
        refs['context_label'].pack(pady=(0, 16), padx=24)

        sep = tk.Frame(modal, height=1, bg='#E0E0E0')
        sep.pack(fill='x', padx=24, pady=(0, 12))

        # Four question blocks
        for var, key, label_key, options_key, labels_key in [
            (age_var,    'age',        'demo_age',         'demo_age_options',         'demo_age_labels'),
            (gender_var, 'gender',     'demo_gender',      'demo_gender_options',      'demo_gender_labels'),
            (native_var, 'native',     'demo_native_lang', 'demo_native_lang_options', 'demo_native_lang_labels'),
            (exp_var,    'experience', 'demo_experience',  'demo_experience_options',  'demo_experience_labels'),
        ]:
            block = tk.Frame(modal, bg=COLOR_BG_WINDOW)
            block.pack(padx=24, pady=(6, 4))
            q_label = tk.Label(
                block, text=s[label_key],
                font=('Helvetica', 14, 'bold'),
                bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
            )
            q_label.pack(anchor='w', pady=(0, 6))
            refs['question_labels'][key] = q_label
            group = OptionButtonGroup(
                block, var,
                options=s[options_key],
                display_labels=s[labels_key],
            )
            group.pack(anchor='w', pady=(0, 4))
            groups[key] = group

        # Submit
        submit_frame = tk.Frame(
            modal, bg=COLOR_BG_PRIMARY_OFF, highlightthickness=0, cursor='arrow',
        )
        refs['submit_label'] = tk.Label(
            submit_frame, text=s['demo_modal_submit'],
            font=('Helvetica', 14, 'bold'),
            bg=COLOR_BG_PRIMARY_OFF, fg=COLOR_FG_PRIMARY_OFF,
            padx=44, pady=12, cursor='arrow',
        )
        refs['submit_label'].pack()
        submit_frame.pack(pady=(18, 22))

        def _submit_click(_e): on_submit()
        submit_frame.bind('<Button-1>', _submit_click)
        refs['submit_label'].bind('<Button-1>', _submit_click)

        def _hover_in(_e):
            if all_answered():
                submit_frame.config(bg=COLOR_BG_PRIMARY_HOT)
                refs['submit_label'].config(bg=COLOR_BG_PRIMARY_HOT)

        def _hover_out(_e):
            if all_answered():
                submit_frame.config(bg=COLOR_BG_PRIMARY_ON)
                refs['submit_label'].config(bg=COLOR_BG_PRIMARY_ON)

        for w in (submit_frame, refs['submit_label']):
            w.bind('<Enter>', _hover_in)
            w.bind('<Leave>', _hover_out)

        self._center_modal(modal, min_w=620, min_h=560)
        # Same render-flush fix as the Likert modal: force a full draw pass
        # before wait_window() takes over, so the demographics form is not blank.
        modal.update()
        modal.lift()
        modal.focus_force()

        shown_utc = now_utc_iso()
        shown_t = time.time()
        self.root.wait_window(modal)

        response_latency_ms = (
            int((state['response_t'] - shown_t) * 1000)
            if state['response_t'] is not None else None
        )
        return {
            'shown_utc':           shown_utc,
            'response_utc':        state['response_utc'],
            'response_latency_ms': response_latency_ms,
            'outcome':             state['outcome'],
            'responses':           state['responses'],
            'language':            state['language'],
        }

    # =========================================================================
    # Screen: Debrief (Step 7b — participant-facing post-session reflection)
    # =========================================================================

    def _show_debrief(self, aborted=False):
        self.current_screen = 'debrief'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['debrief_title'], s['debrief_subtitle'])

        # Two Likert questions + two optional text fields
        form = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        form.pack(padx=40, pady=4, fill='x')

        # Difficulty (Likert 1-7)
        self._add_section_label(form, s['debrief_difficulty'])
        difficulty_var = tk.IntVar(
            value=self._draft_debrief.get('difficulty', 0))
        LikertButtonGroup(form, difficulty_var).pack(anchor='w', pady=(0, 4))
        anchors_d = tk.Frame(form, bg=COLOR_BG_WINDOW)
        anchors_d.pack(fill='x', pady=(0, 4))
        tk.Label(anchors_d, text=s['debrief_difficulty_low'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED
                ).pack(side='left', padx=(5, 0))
        tk.Label(anchors_d, text=s['debrief_difficulty_high'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED
                ).pack(side='right', padx=(0, 5))

        # Disruption (Likert 1-7)
        self._add_section_label(form, s['debrief_disruption'])
        disruption_var = tk.IntVar(
            value=self._draft_debrief.get('disruption', 0))
        LikertButtonGroup(form, disruption_var).pack(anchor='w', pady=(0, 4))
        anchors_di = tk.Frame(form, bg=COLOR_BG_WINDOW)
        anchors_di.pack(fill='x', pady=(0, 4))
        tk.Label(anchors_di, text=s['debrief_disruption_low'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED
                ).pack(side='left', padx=(5, 0))
        tk.Label(anchors_di, text=s['debrief_disruption_high'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED
                ).pack(side='right', padx=(0, 5))

        # Purpose (optional text)
        self._add_section_label(form, s['debrief_purpose'])
        purpose_var = tk.StringVar(value=self._draft_debrief.get('purpose', ''))
        purpose_entry = BorderedEntry(form, textvariable=purpose_var,
                                       font=('Helvetica', 11))
        purpose_entry.pack(fill='x')
        tk.Label(form, text=s['debrief_purpose_hint'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED
                ).pack(anchor='w', pady=(2, 8))

        # Other (optional text)
        self._add_section_label(form, s['debrief_other'])
        other_var = tk.StringVar(value=self._draft_debrief.get('other', ''))
        other_entry = BorderedEntry(form, textvariable=other_var,
                                     font=('Helvetica', 11))
        other_entry.pack(fill='x')
        tk.Label(form, text=s['debrief_other_hint'],
                 font=('Helvetica', 9, 'italic'),
                 bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED
                ).pack(anchor='w', pady=(2, 4))

        def on_change(*_a):
            # Persist draft on every change
            self._draft_debrief['difficulty'] = difficulty_var.get()
            self._draft_debrief['disruption'] = disruption_var.get()
            self._draft_debrief['purpose']    = purpose_var.get()
            self._draft_debrief['other']      = other_var.get()
            # Both Likerts required; text fields optional
            valid = (difficulty_var.get() > 0 and disruption_var.get() > 0)
            finish_btn.set_enabled(valid)

        difficulty_var.trace_add('write', on_change)
        disruption_var.trace_add('write', on_change)
        purpose_var.trace_add('write', on_change)
        other_var.trace_add('write', on_change)

        def on_finish():
            if not (difficulty_var.get() > 0 and disruption_var.get() > 0):
                return
            debrief_record = {
                'session_id':       self.session_id,
                'participant_id':   self.participant_id,
                'recorded_utc':     now_utc_iso(),
                'recorded_language': self.language,
                'difficulty_likert_1_7': difficulty_var.get(),
                'disruption_likert_1_7': disruption_var.get(),
                'suspected_purpose':     purpose_var.get().strip(),
                'other_comments':        other_var.get().strip(),
            }
            # Write to session_dir as a standalone JSON file
            (self.session_dir / 'debrief.json').write_text(
                json.dumps(debrief_record, indent=2, ensure_ascii=False)
            )
            # Also append to survey.jsonl for the unified prompt timeline
            append_survey_jsonl(self.session_dir, {
                'type':           'debrief',
                'timestamp_utc':  debrief_record['recorded_utc'],
                'language':       debrief_record['recorded_language'],
                'responses': {
                    'difficulty_likert_1_7': debrief_record['difficulty_likert_1_7'],
                    'disruption_likert_1_7': debrief_record['disruption_likert_1_7'],
                    'suspected_purpose':     debrief_record['suspected_purpose'],
                    'other_comments':        debrief_record['other_comments'],
                },
            })
            self.debrief = debrief_record
            self._show_complete(aborted=False)

        finish_btn = PrimaryButton(self.root, s['debrief_submit'], on_finish)
        finish_btn.pack(pady=(20, 22))
        on_change()  # set initial enabled state from draft

    # =========================================================================
    # Session file validation
    # =========================================================================

    def _validate_session_files(self):
        """
        Check that expected files exist in self.session_dir and look healthy.
        Returns: list of dicts {name, status, message, size_bytes_or_None}
        where status is 'pass' | 'fail' | 'warn'.
        """
        results = []

        def add(name, status, message, size=None):
            results.append({'name': name, 'status': status,
                            'message': message, 'size_bytes': size})

        # Always-expected files (written by controller itself)
        always_expected = [
            ('session_metadata.json', 'JSON, controller'),
            ('survey.jsonl',          'JSONL, prompts + debrief'),
            ('debrief.json',          'JSON, debrief responses'),
        ]
        for fname, _desc in always_expected:
            fpath = self.session_dir / fname
            if not fpath.exists():
                add(fname, 'fail', 'missing')
                continue
            size = fpath.stat().st_size
            if size == 0:
                add(fname, 'fail', 'empty', size)
                continue
            # JSON validity check for .json files
            if fname.endswith('.json'):
                try:
                    json.loads(fpath.read_text())
                    add(fname, 'pass', 'present, valid JSON', size)
                except json.JSONDecodeError as e:
                    add(fname, 'fail', f'invalid JSON: {e}', size)
            else:
                add(fname, 'pass', 'present', size)

        # Consent record (filename varies by participant + timestamp)
        consent_txts = list(self.session_dir.glob('consent_*.txt'))
        consent_jsons = list(self.session_dir.glob('consent_*.json'))
        if not consent_txts:
            add('consent_*.txt', 'fail', 'missing legal consent record')
        else:
            size = consent_txts[0].stat().st_size
            add(consent_txts[0].name, 'pass', 'consent record present', size)
        if not consent_jsons:
            add('consent_*.json', 'fail', 'missing structured consent record')
        else:
            size = consent_jsons[0].stat().st_size
            add(consent_jsons[0].name, 'pass', 'structured consent present', size)

        # Acquisition streams (written by start_session.sh; skip in dry-run)
        if not self.dry_run:
            stream_files = [
                'polar.jsonl',
                'input.jsonl',
                'focused_app.jsonl',
                'osquery.jsonl',
                'recording.mp4',
            ]
            for fname in stream_files:
                fpath = self.session_dir / fname
                if not fpath.exists():
                    add(fname, 'fail', 'missing')
                    continue
                size = fpath.stat().st_size
                if size == 0:
                    add(fname, 'warn', 'empty (stream may have produced no data)',
                        size)
                else:
                    add(fname, 'pass', 'present', size)

        # Survey.jsonl content check: count prompt records
        survey_path = self.session_dir / 'survey.jsonl'
        if survey_path.exists():
            try:
                lines = survey_path.read_text().strip().splitlines()
                prompt_count = sum(1 for line in lines
                                    if line and json.loads(line).get('type')
                                    == 'prompt')
                debrief_count = sum(1 for line in lines
                                     if line and json.loads(line).get('type')
                                     == 'debrief')
                expected_prompts = len(self._prompt_schedule)
                if prompt_count < expected_prompts:
                    add('survey.jsonl content', 'warn',
                        f'{prompt_count} prompts recorded, '
                        f'{expected_prompts} expected')
                else:
                    add('survey.jsonl content', 'pass',
                        f'{prompt_count} prompts + {debrief_count} debrief')
            except Exception as e:
                add('survey.jsonl content', 'fail', f'parse error: {e}')

        return results

    # =========================================================================
    # Screen: Complete (operator-facing consolidation summary)
    # =========================================================================

    def _show_complete(self, aborted=False):
        self.current_screen = 'complete'
        self._clear()
        self._add_top_bar()
        s = self._s()
        self._add_title(s['consolidation_title'], s['consolidation_subtitle'])

        # Session dir prominently displayed
        tk.Label(
            self.root, text=str(self.session_dir),
            font=('Helvetica', 11, 'bold'),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_TITLE,
            wraplength=620, justify='center',
        ).pack(pady=(0, 14), padx=24)

        # Update metadata + write session_end record
        actual_duration = (time.time() - self.session_start_t
                           if self.session_start_t else 0)
        prompt_results = getattr(self, '_prompt_results', [])
        prompt_summary = {
            'total': len(prompt_results),
            'submitted': sum(1 for r in prompt_results
                              if r['outcome'] == 'submitted'),
            'skipped': sum(1 for r in prompt_results
                            if r['outcome'] == 'skipped'),
            'dismissed': sum(1 for r in prompt_results
                              if r['outcome'] == 'dismissed'),
        }
        append_survey_jsonl(self.session_dir, {
            'type': 'session_end',
            'timestamp_utc': now_utc_iso(),
            'reason': 'aborted' if aborted else 'completed',
            'prompt_summary': prompt_summary,
        })
        metadata_path = self.session_dir / 'session_metadata.json'
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text())
            except json.JSONDecodeError:
                metadata = {}
            metadata.update({
                'session_end_utc': now_utc_iso(),
                'duration_actual_sec': round(actual_duration, 1),
                'session_exit_code': self.session_exit_code,
                'prompt_summary': prompt_summary,
                'debrief': getattr(self, 'debrief', None),
                'aborted': aborted,
            })
            metadata_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False)
            )

        # Run file validation and render results
        validation_results = self._validate_session_files()
        any_failures = any(r['status'] == 'fail' for r in validation_results)
        any_warnings = any(r['status'] == 'warn' for r in validation_results)

        # Banner
        if any_failures:
            banner_text = s['consolidation_issues']
            banner_fg = COLOR_BG_DANGER
        elif any_warnings:
            banner_text = s['consolidation_issues']
            banner_fg = COLOR_TEXT_BODY
        else:
            banner_text = s['consolidation_all_ok']
            banner_fg = COLOR_BG_PRIMARY_ON

        tk.Label(
            self.root, text=banner_text,
            font=('Helvetica', 12, 'italic'),
            bg=COLOR_BG_WINDOW, fg=banner_fg,
            wraplength=620, justify='center',
        ).pack(pady=(0, 14), padx=24)

        # Validation result rows in a scrollable area (just in case it
        # overflows on small screens)
        results_outer = tk.Frame(self.root, bg=COLOR_BG_WINDOW)
        results_outer.pack(padx=40, pady=(0, 14), fill='both', expand=True)

        for result in validation_results:
            row = tk.Frame(results_outer, bg=COLOR_BG_WINDOW)
            row.pack(fill='x', pady=3)

            # Status badge
            if result['status'] == 'pass':
                badge_text, badge_bg = '✓', COLOR_BG_PRIMARY_ON
            elif result['status'] == 'fail':
                badge_text, badge_bg = '✗', COLOR_BG_DANGER
            else:
                badge_text, badge_bg = '!', '#D97706'  # warn = amber

            badge_cell = tk.Frame(row, width=28, height=28, bg=badge_bg,
                                   highlightthickness=0)
            badge_cell.pack_propagate(False)
            badge_cell.pack(side='left', padx=(0, 12))
            tk.Label(badge_cell, text=badge_text,
                     font=('Helvetica', 13, 'bold'),
                     bg=badge_bg, fg='#FFFFFF').pack(expand=True, fill='both')

            # Filename + message + size
            text_col = tk.Frame(row, bg=COLOR_BG_WINDOW)
            text_col.pack(side='left', fill='x', expand=True)

            name_line = result['name']
            if result.get('size_bytes') is not None:
                size = result['size_bytes']
                if size > 1024 * 1024:
                    size_str = f' ({size / (1024 * 1024):.1f} MB)'
                elif size > 1024:
                    size_str = f' ({size / 1024:.1f} KB)'
                else:
                    size_str = f' ({size} B)'
                name_line += size_str

            tk.Label(text_col, text=name_line,
                     font=('Helvetica', 11, 'bold'),
                     bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_BODY,
                     anchor='w').pack(fill='x')
            tk.Label(text_col, text=result['message'],
                     font=('Helvetica', 9),
                     bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
                     anchor='w').pack(fill='x')

        # Brief duration summary
        mins = int(actual_duration // 60)
        secs = int(actual_duration % 60)
        tk.Label(
            self.root,
            text=f"{s['complete_duration']}: {mins:02d}:{secs:02d}   "
                 f"|   {s['complete_prompts']}: "
                 f"{prompt_summary['submitted']}/"
                 f"{prompt_summary['skipped']}/"
                 f"{prompt_summary['dismissed']} "
                 f"(submitted/skipped/dismissed)",
            font=('Helvetica', 10),
            bg=COLOR_BG_WINDOW, fg=COLOR_TEXT_MUTED,
        ).pack(pady=(4, 12))

        # Close button
        def on_close():
            self.root.destroy()

        close_btn = PrimaryButton(self.root, s['consolidation_close'], on_close)
        close_btn.set_enabled(True)
        close_btn.pack(pady=(6, 24))

    # =========================================================================
    # Window close handler
    # =========================================================================

    def _on_window_close(self):
        if self.current_screen == 'running':
            from tkinter import messagebox
            s = self._s()
            if messagebox.askyesno(
                s['running_abort_confirm_title'],
                s['running_abort_confirm_msg'],
            ):
                self._terminate_subprocesses(why='window_closed')
                self.root.destroy()
        else:
            # Make sure the widget is killed even if controller closes
            # outside the running state (e.g. user closes from complete
            # screen without clicking the Close button).
            self._terminate_checklist()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


# =============================================================================
# Entry point
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        description='Phase 1 session controller (Step 7a).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('--duration', type=int, default=DEFAULT_DURATION_SEC,
                    help=f'Session duration in seconds (default {DEFAULT_DURATION_SEC} = 26 min).')
    ap.add_argument('--language', type=str, default='en',
                    choices=SUPPORTED_LANGUAGES,
                    help='Default UI language. Can be toggled via flag in UI.')
    ap.add_argument('--dry-run', action='store_true',
                    help='Do not actually spawn start_session.sh or survey_prompter; '
                         'use sleep processes as stand-ins for testing the UI flow.')
    args = ap.parse_args()

    # Sanity check on dependencies
    missing = []
    # start_session.sh is needed for real runs (not dry-run)
    if not args.dry_run and not START_SESSION_SH.exists():
        missing.append(str(START_SESSION_SH))
    if missing:
        print('ERROR: required scripts not found:', file=sys.stderr)
        for m in missing:
            print(f'  - {m}', file=sys.stderr)
        sys.exit(1)

    # Ensure directories exist
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    PARTICIPANTS_DIR.mkdir(parents=True, exist_ok=True)

    controller = SessionController(
        duration_sec=args.duration,
        initial_language=args.language,
        dry_run=args.dry_run,
    )
    controller.run()


if __name__ == '__main__':
    main()
