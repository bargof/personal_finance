from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

# ═══════════════════════════════════════════
# Definición de la configuración con validación
# ═══════════════════════════════════════════


class Settings(BaseSettings):
    """
    Configuración centralizada de la aplicación.

    Carga automáticamente desde variables de entorno y archivos .env.
    Los parámetros financieros que el usuario puede cambiar en caliente
    NO viven aquí: se guardan en la tabla `configuracion` de SQLite.
    """

    # --- Entorno ---
    environment: str = Field(
        default="development",
        description="Entorno de ejecución: development, staging, production",
    )
    debug: bool = Field(default=False, description="Modo debug activo")
    base_dir: Path = Path(__file__).resolve().parents[3]

    # --- Base de datos ---
    data_dir: Path = Path("data")
    db_filename: str = Field(
        default="finanzas.db",
        description="Nombre del archivo SQLite",
    )

    # --- Fuente de migración inicial ---
    excel_source: Path = Field(
        default=Path("../Sistema_Financiero_Personal.xlsx"),
        description="Excel original desde el que se siembra la base",
    )

    # --- Reglas de negocio por defecto (semilla de la tabla configuracion) ---
    moneda: str = Field(default="MXN", description="Moneda de trabajo")
    meta_ahorro_inversion: float = Field(
        default=0.20,
        ge=0,
        le=1,
        description="Proporción objetivo de ingreso destinada a ahorro + inversión",
    )
    meses_fondo_emergencia: int = Field(
        default=6,
        ge=1,
        description="Meses de gasto esencial que debe cubrir el fondo de emergencia",
    )
    max_deseos: float = Field(
        default=0.30,
        ge=0,
        le=1,
        description="Proporción máxima deseable del gasto destinada a 'Deseo'",
    )
    umbral_gasto_pequeno: float = Field(
        default=200.0,
        gt=0,
        description="Monto por debajo del cual un gasto cuenta como microgasto",
    )
    alerta_presupuesto: float = Field(
        default=0.90,
        gt=0,
        le=1,
        description="% de presupuesto usado a partir del cual se marca 'Atención'",
    )
    dia_inicio_ciclo: int = Field(
        default=1,
        ge=1,
        le=28,
        description="Día en que arranca el ciclo mensual",
    )

    # --- Logging ---
    log_level: str = "INFO"
    log_file: str = "logs/app.log"

    # --- Configuración de pydantic-settings ---
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",  # Ignora variables de entorno no definidas aquí
    }

    @property
    def db_path(self) -> Path:
        """Ruta absoluta al archivo SQLite."""
        return self.base_dir / self.data_dir / self.db_filename

    @property
    def log_file_path(self) -> Path:
        """Ruta absoluta al archivo de log."""
        return self.base_dir / self.log_file

    @property
    def excel_source_path(self) -> Path:
        """Ruta absoluta al Excel de origen."""
        if self.excel_source.is_absolute():
            return self.excel_source
        return (self.base_dir / self.excel_source).resolve()


# ═══════════════════════════════════════════
# Patrón singleton: una sola instancia de configuración
# ═══════════════════════════════════════════
def get_settings() -> Settings:
    """Retorna la configuración de la aplicación."""
    return Settings()


settings = get_settings()
