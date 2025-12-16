document.addEventListener('DOMContentLoaded', () => {
    const phoneInput = document.getElementById('regPhone');

    // Important: set both loadUtilsOnInit and utilsScript so the loader knows the exact URL
    const iti = window.intlTelInput(document.getElementById('regPhone'), {
    initialCountry: "us",
    separateDialCode: true,
    nationalMode: true,
    autoPlaceholder: "aggressive",
    placeholderNumberType: "MOBILE",
    loadUtilsOnInit: false // because we loaded utils.js statically
    });

    function normalizeIfInternational(raw) {
        const cleaned = raw.replace(/[\s().-]/g, '');
        if (/^\+\d{4,15}$/.test(cleaned)) {
            // Let the lib parse +E.164, auto-sets the country and fills national part
            iti.setNumber(cleaned);
        }
    }

    btn.addEventListener('click', async (e) => {
    e.preventDefault();
    await iti.promise.catch(() => {}); // continue even if utils failed

    phoneInput.value = phoneInput.value.trim();
    normalizeIfInternational(phoneInput.value);

    // Try official validation first (works only if utils actually loaded)
    let valid = false;
    try { valid = iti.isValidNumber(); } catch {}

    if (!valid) {
        // Fallback: do a minimal sanity check so users aren’t blocked if utils failed
        const { dialCode, iso2 } = iti.getSelectedCountryData() || {};
        const national = phoneInput.value.replace(/[^\d]/g, '');
        // crude per-country fallback; expand as needed
        const minLen = (iso2 === 'us' || iso2 === 'ca') ? 10 : 6;
        const ok = dialCode && national.length >= minLen;
        if (!ok) {
        const reason = typeof iti.getValidationError === 'function' ? iti.getValidationError() : undefined;
        console.warn('Validation fallback used. Lib reason code:', reason);
        alert('Please enter a valid phone number.');
        return;
        }
    }

    // E.164 for backend
    const e164 = iti.getNumber(); // if utils absent, this can be "", so also build manually:
    const { dialCode } = iti.getSelectedCountryData() || {};
    const nationalDigits = phoneInput.value.replace(/[^\d]/g,'');
    const numberToSend = e164 || (dialCode ? `+${dialCode}${nationalDigits}` : '');

    console.log(window.intlTelInputUtils);

    const natl = window.intlTelInputUtils
    ? iti.getNumber(window.intlTelInputUtils.numberFormat.NATIONAL)
    : phoneInput.value; // fallback if utils didn't load

    console.log(iti);

    console.log(iti.selectedCountryData.iso2);

    const payload = {
        phone_e164: numberToSend,
        phone_national: nationalDigits,
        phone_formatted: natl,
        country_iso2: iti.selectedCountryData.iso2,
        country_name: iti.selectedCountryData.name,
        dial_code: dialCode,
    };

    console.log(payload);
    });
});