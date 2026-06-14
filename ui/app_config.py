class AppConfig:
    PAGE_TITLE = "Gestor de Proyectos SEN"
    PAGE_ICON = "⚡"

    APP_TITLE = "Gestor de Proyectos SEN para Modelo Eléctrico"
    APP_SUBTITLE = "<em>software</em> PowerFactory & EMTP"

    PROJECT_TYPE_ORDER = [
        "transmission",
        "generation",
        "bess",
        "der",
    ]

    PROJECT_TYPE_LABELS = {
        "transmission": "Transmisión",
        "generation": "Generación",
        "bess": "BESS",
        "der": "DER",
    }

    TABLE_HIDDEN_COLUMNS = [
        "project_discriminator",
    ]

    COLORS = {
        "background": "#F4F6F5",
        "background_alt": "#EEF3F1",
        "surface": "#FAFBFA",
        "surface_soft": "#F0F5F2",
        "surface_green": "#E7F3EA",
        "surface_blue": "#E3F0F3",
        "surface_orange": "#F8ECE2",
        "text": "#303437",
        "text_muted": "#6D7477",
        "border": "#D8E0DC",
        "green": "#6FAE2E",
        "green_dark": "#2F8A57",
        "blue": "#007C92",
        "blue_dark": "#005F73",
        "orange": "#E96F24",
        "red_orange": "#D94A27",
        "yellow_green": "#B5C832",
    }
