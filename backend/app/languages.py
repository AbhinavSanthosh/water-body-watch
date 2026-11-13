from app.models import LanguageOption

# Bhashini uses ISO-639-1 codes. English is always the pivot language.
LANGUAGES: list[LanguageOption] = [
    LanguageOption(code="kn", label="Kannada"),
    LanguageOption(code="hi", label="Hindi"),
    LanguageOption(code="ta", label="Tamil"),
    LanguageOption(code="te", label="Telugu"),
    LanguageOption(code="ml", label="Malayalam"),
    LanguageOption(code="mr", label="Marathi"),
    LanguageOption(code="bn", label="Bengali"),
    LanguageOption(code="gu", label="Gujarati"),
    LanguageOption(code="pa", label="Punjabi"),
    LanguageOption(code="or", label="Odia"),
    LanguageOption(code="en", label="English"),
]

LANGUAGE_CODES = {language.code for language in LANGUAGES}
