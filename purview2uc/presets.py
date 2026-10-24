"""Built-in alias presets: Purview classification -> Unity Catalog tag(s).

`databricks_class` maps Microsoft's built-in classifications to the system governed tags used by
Databricks Data Classification (class.*). Columns migrated from Purview then look exactly like
columns classified by Databricks itself, so ABAC policies and Catalog Explorer filters written for
class.* work on both. Only clear one-to-one equivalences are listed; anything else falls back to
a tag with the classification name itself (or can be mapped in tags.aliases).
"""

DATABRICKS_CLASS = {
    "MICROSOFT.PERSONAL.EMAIL": "class.email_address",
    "MICROSOFT.PERSONAL.NAME": "class.name",
    "MICROSOFT.PERSONAL.DATE_OF_BIRTH": "class.date_of_birth",
    "MICROSOFT.PERSONAL.AGE": "class.age",
    "MICROSOFT.PERSONAL.GENDER": "class.gender",
    "MICROSOFT.PERSONAL.ETHNIC_GROUP": "class.ethnicity",
    "MICROSOFT.PERSONAL.IPADDRESS": "class.ip_address",
    "MICROSOFT.MISCELLANEOUS.IPADDRESS": "class.ip_address",
    "MICROSOFT.PERSONAL.PHYSICALADDRESS": "class.street_address",
    "MICROSOFT.PERSONAL.GEOLOCATION": "class.location",
    "MICROSOFT.PERSONAL.EU.GPS_COORDINATES": "class.geo_coordinates",
    "MICROSOFT.PERSONAL.US.PHONE_NUMBER": "class.phone_number",
    "MICROSOFT.PERSONAL.EU.PHONE_NUMBER": "class.phone_number",
    "MICROSOFT.PERSONAL.EU.MOBILE_PHONE_NUMBER": "class.phone_number",
    "MICROSOFT.PERSONAL.JAPANESE.MY.NUMBER.PERSONAL": "class.jp_my_number",
    "MICROSOFT.FINANCIAL.CREDIT_CARD_NUMBER": "class.credit_card",
    "MICROSOFT.FINANCIAL.INTERNATIONAL.BANK_ACCOUNT_NUMBER": "class.iban_code",
    "MICROSOFT.FINANCIAL.SWIFT_CODE": "class.swift_code",
    "MICROSOFT.FINANCIAL.US.BANK_ACCOUNT_NUMBER": "class.us_bank_number",
    "MICROSOFT.FINANCIAL.ITALY.FISCAL.CODE": "class.it_codice_fiscale",
    "MICROSOFT.GOVERNMENT.BRAZIL.CPF_NUMBER": "class.br_cpf",
    "MICROSOFT.GOVERNMENT.BRAZIL.NATIONAL_ID_CARD": "class.br_rg",
    "MICROSOFT.GOVERNMENT.US.SOCIAL_SECURITY_NUMBER": "class.us_ssn",
    "MICROSOFT.GOVERNMENT.US.PASSPORT_NUMBER": "class.us_passport",
    "MICROSOFT.GOVERNMENT.US.DRIVERS_LICENSE_NUMBER": "class.us_driver_license",
    "MICROSOFT.GOVERNMENT.US.INDIVIDUAL_TAXPAYER_IDENTIFICATION_NUMBER": "class.us_itin",
    "MICROSOFT.GOVERNMENT.UK.NATIONAL_INSURANCE_NUMBER": "class.uk_nino",
    "MICROSOFT.GOVERNMENT.UK.NATIONAL_HEALTH_SERVICE_NUMBER": "class.uk_nhs",
    "MICROSOFT.GOVERNMENT.UK.PASSPORT_NUMBER": "class.passport",
    "MICROSOFT.GOVERNMENT.EU.PASSPORT_NUMBER": "class.passport",
    "MICROSOFT.GOVERNMENT.UK.DRIVERS_LICENSE_NUMBER": "class.driver_license",
    "MICROSOFT.GOVERNMENT.EU.DRIVERS_LICENSE_NUMBER": "class.driver_license",
    "MICROSOFT.GOVERNMENT.FRANCE.SOCIAL_SECURITY_NUMBER": "class.fr_nir",
    "MICROSOFT.GOVERNMENT.DENMARK.PERSONAL_ID_NUMBER": "class.dk_cpr",
    "MICROSOFT.GOVERNMENT.CANADA.PERSONAL_HEALTH_IDENTIFICATION_NUMBER": "class.ca_health_number",
}

# class.* são governed tags sem lista de valores: só aceitam a chave (valor vazio).
PRESETS = {
    "databricks_class": {name: [{"key": key, "value": ""}] for name, key in DATABRICKS_CLASS.items()},
}
