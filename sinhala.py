"""Sinhala text handling: romanisation, normalisation and fuzzy command matching.

Whisper writes Sinhala speech in whatever script it feels like — Sinhala, Latin,
Devanagari, Gujarati, Malayalam — while often getting the *sounds* right. Matching on
script is therefore hopeless and matching on sound works. Everything here reduces text
to one rough phonetic form in Latin letters so that "දැන් වෙලාව කීයද",
"Den belaave keyed" and "अब वेलाව कියද" all compare as the same command.
"""
import re
import unicodedata

# Sinhala independent vowels.
VOWELS = {
    'අ': 'a', 'ආ': 'a', 'ඇ': 'a', 'ඈ': 'a', 'ඉ': 'i', 'ඊ': 'i', 'උ': 'u', 'ඌ': 'u',
    'ඍ': 'ru', 'ඎ': 'ru', 'ඏ': 'lu', 'එ': 'e', 'ඒ': 'e', 'ඓ': 'ai', 'ඔ': 'o',
    'ඕ': 'o', 'ඖ': 'au',
}

# Consonants, mapped to their bare sound. Aspirates collapse onto the plain form and
# retroflex/dental pairs collapse together: the distinctions carry no weight when the
# input is a speech recogniser's guess.
CONSONANTS = {
    'ක': 'k', 'ඛ': 'k', 'ග': 'g', 'ඝ': 'g', 'ඞ': 'n', 'ඟ': 'g',
    'ච': 'c', 'ඡ': 'c', 'ජ': 'j', 'ඣ': 'j', 'ඤ': 'n', 'ඥ': 'n', 'ඦ': 'j',
    'ට': 't', 'ඨ': 't', 'ඩ': 'd', 'ඪ': 'd', 'ණ': 'n', 'ඬ': 'd',
    'ත': 't', 'ථ': 't', 'ද': 'd', 'ධ': 'd', 'න': 'n', 'ඳ': 'd',
    'ප': 'p', 'ඵ': 'p', 'බ': 'b', 'භ': 'b', 'ම': 'm', 'ඹ': 'b',
    'ය': 'y', 'ර': 'r', 'ල': 'l', 'ව': 'v', 'ශ': 's', 'ෂ': 's', 'ස': 's',
    'හ': 'h', 'ළ': 'l', 'ෆ': 'f',
}

# Dependent vowel signs.
SIGNS = {
    'ා': 'a', 'ැ': 'a', 'ෑ': 'a', 'ි': 'i', 'ී': 'i',
    'ු': 'u', 'ූ': 'u', 'ෘ': 'ru', 'ෙ': 'e', 'ේ': 'e',
    'ෛ': 'ai', 'ො': 'o', 'ෝ': 'o', 'ෞ': 'au', 'ෟ': 'lu',
}
HAL = '්'          # suppresses the inherent vowel
ANUSVARA = 'ං'     # ං
VISARGA = 'ඃ'      # ඃ

# Tamil. This one matters most: Whisper has far more Tamil than Sinhala training data, so
# Sinhala speech from Sri Lanka frequently comes back in Tamil script with the sounds
# intact. Eight of twenty test commands landed here.
TAMIL = {
    'க': 'k', 'ங': 'n', 'ச': 's', 'ஞ': 'n', 'ட': 't', 'ண': 'n', 'த': 't', 'ந': 'n',
    'ப': 'p', 'ம': 'm', 'ய': 'y', 'ர': 'r', 'ல': 'l', 'வ': 'v', 'ழ': 'l', 'ள': 'l',
    'ற': 'r', 'ன': 'n', 'ஜ': 'j', 'ஷ': 's', 'ஸ': 's', 'ஹ': 'h', 'ஶ': 's',
}
TAMIL_VOWELS = {
    'அ': 'a', 'ஆ': 'a', 'இ': 'i', 'ஈ': 'i', 'உ': 'u', 'ஊ': 'u', 'எ': 'e', 'ஏ': 'e',
    'ஐ': 'ai', 'ஒ': 'o', 'ஓ': 'o', 'ஔ': 'au', 'ஃ': 'h',
}
TAMIL_SIGNS = {
    'ா': 'a', 'ி': 'i', 'ீ': 'i', 'ு': 'u', 'ூ': 'u', 'ெ': 'e', 'ே': 'e', 'ை': 'ai',
    'ொ': 'o', 'ோ': 'o', 'ௌ': 'au',
}

# Telugu, Kannada and Bengali, in case Whisper reaches for those too.
TELUGU = {
    'క': 'k', 'ఖ': 'k', 'గ': 'g', 'చ': 'c', 'జ': 'j', 'ట': 't', 'డ': 'd', 'ణ': 'n',
    'త': 't', 'థ': 't', 'ద': 'd', 'ధ': 'd', 'న': 'n', 'ప': 'p', 'బ': 'b', 'భ': 'b',
    'మ': 'm', 'య': 'y', 'ర': 'r', 'ల': 'l', 'వ': 'v', 'శ': 's', 'ష': 's', 'స': 's',
    'హ': 'h', 'ళ': 'l',
    'ಕ': 'k', 'ಖ': 'k', 'ಗ': 'g', 'ಚ': 'c', 'ಜ': 'j', 'ಟ': 't', 'ಡ': 'd', 'ಣ': 'n',
    'ತ': 't', 'ಥ': 't', 'ದ': 'd', 'ಧ': 'd', 'ನ': 'n', 'ಪ': 'p', 'ಬ': 'b', 'ಭ': 'b',
    'ಮ': 'm', 'ಯ': 'y', 'ರ': 'r', 'ಲ': 'l', 'ವ': 'v', 'ಶ': 's', 'ಷ': 's', 'ಸ': 's',
    'ಹ': 'h', 'ಳ': 'l',
    'ক': 'k', 'গ': 'g', 'চ': 'c', 'জ': 'j', 'ট': 't', 'ড': 'd', 'ণ': 'n', 'ত': 't',
    'দ': 'd', 'ন': 'n', 'প': 'p', 'ব': 'b', 'ম': 'm', 'য': 'y', 'র': 'r', 'ল': 'l',
    'শ': 's', 'ষ': 's', 'স': 's', 'হ': 'h',
}
TELUGU_VOWELS = {
    'అ': 'a', 'ఆ': 'a', 'ఇ': 'i', 'ఈ': 'i', 'ఉ': 'u', 'ఎ': 'e', 'ఏ': 'e', 'ఒ': 'o',
    'ಅ': 'a', 'ಆ': 'a', 'ಇ': 'i', 'ಈ': 'i', 'ಉ': 'u', 'ಎ': 'e', 'ಏ': 'e', 'ಒ': 'o',
    'অ': 'a', 'আ': 'a', 'ই': 'i', 'উ': 'u', 'এ': 'e', 'ও': 'o',
}
TELUGU_SIGNS = {
    'ా': 'a', 'ి': 'i', 'ీ': 'i', 'ు': 'u', 'ూ': 'u', 'ె': 'e', 'ే': 'e', 'ొ': 'o',
    'ಾ': 'a', 'ಿ': 'i', 'ೀ': 'i', 'ು': 'u', 'ೂ': 'u', 'ೆ': 'e', 'ೇ': 'e', 'ೊ': 'o',
    'া': 'a', 'ি': 'i', 'ী': 'i', 'ু': 'u', 'ূ': 'u', 'ে': 'e', 'ো': 'o',
}

# Every script's vowel-suppressing mark.
VIRAMAS = {'්', '्', '્', '്', '੍', '்',
           '్', '್', '্'}

# Other Indic scripts Whisper falls back to, reduced to the same Latin sounds. Only the
# consonants and vowels that actually turn up are listed; anything else is dropped.
OTHER_INDIC = {
    # Devanagari
    'क': 'k', 'ख': 'k', 'ग': 'g', 'घ': 'g', 'च': 'c', 'छ': 'c', 'ज': 'j', 'झ': 'j',
    'ट': 't', 'ठ': 't', 'ड': 'd', 'ढ': 'd', 'ण': 'n', 'त': 't', 'थ': 't', 'द': 'd',
    'ध': 'd', 'न': 'n', 'प': 'p', 'फ': 'p', 'ब': 'b', 'भ': 'b', 'म': 'm', 'य': 'y',
    'र': 'r', 'ल': 'l', 'व': 'v', 'श': 's', 'ष': 's', 'स': 's', 'ह': 'h', 'ळ': 'l',
    'अ': 'a', 'आ': 'a', 'इ': 'i', 'ई': 'i', 'उ': 'u', 'ऊ': 'u', 'ए': 'e', 'ऐ': 'ai',
    'ओ': 'o', 'औ': 'au',
    'ा': 'a', 'ि': 'i', 'ी': 'i', 'ु': 'u', 'ू': 'u', 'े': 'e', 'ै': 'ai', 'ो': 'o',
    'ौ': 'au', '्': '',
    # Gujarati
    'ક': 'k', 'ખ': 'k', 'ગ': 'g', 'ચ': 'c', 'જ': 'j', 'ટ': 't', 'ડ': 'd', 'ણ': 'n',
    'ત': 't', 'થ': 't', 'દ': 'd', 'ધ': 'd', 'ન': 'n', 'પ': 'p', 'બ': 'b', 'ભ': 'b',
    'મ': 'm', 'ય': 'y', 'ર': 'r', 'લ': 'l', 'વ': 'v', 'શ': 's', 'ષ': 's', 'સ': 's',
    'હ': 'h', 'ળ': 'l', 'અ': 'a', 'આ': 'a', 'ઇ': 'i', 'ઈ': 'i', 'ઉ': 'u', 'એ': 'e',
    'ઓ': 'o', 'ા': 'a', 'િ': 'i', 'ી': 'i', 'ુ': 'u', 'ૂ': 'u', 'ે': 'e', 'ો': 'o',
    '્': '',
    # Malayalam
    'ക': 'k', 'ഖ': 'k', 'ഗ': 'g', 'ച': 'c', 'ജ': 'j', 'ട': 't', 'ഡ': 'd', 'ണ': 'n',
    'ത': 't', 'ഥ': 't', 'ദ': 'd', 'ധ': 'd', 'ന': 'n', 'പ': 'p', 'ബ': 'b', 'മ': 'm',
    'യ': 'y', 'ര': 'r', 'ല': 'l', 'വ': 'v', 'ശ': 's', 'ഷ': 's', 'സ': 's', 'ഹ': 'h',
    'ള': 'l', 'അ': 'a', 'ആ': 'a', 'ഇ': 'i', 'ഉ': 'u', 'എ': 'e', 'ഒ': 'o',
    'ാ': 'a', 'ി': 'i', 'ീ': 'i', 'ു': 'u', 'ൂ': 'u', 'െ': 'e', 'േ': 'e', 'ൊ': 'o',
    '്': '',
    # Gurmukhi
    'ਕ': 'k', 'ਗ': 'g', 'ਚ': 'c', 'ਜ': 'j', 'ਟ': 't', 'ਡ': 'd', 'ਤ': 't', 'ਦ': 'd',
    'ਨ': 'n', 'ਪ': 'p', 'ਬ': 'b', 'ਮ': 'm', 'ਯ': 'y', 'ਰ': 'r', 'ਲ': 'l', 'ਵ': 'v',
    'ਸ': 's', 'ਹ': 'h', 'ਅ': 'a', 'ਆ': 'a', 'ਇ': 'i', 'ਉ': 'u', 'ਏ': 'e', 'ਓ': 'o',
    'ਾ': 'a', 'ਿ': 'i', 'ੀ': 'i', 'ੁ': 'u', 'ੂ': 'u', 'ੇ': 'e', 'ੋ': 'o', '੍': '',
}

# Latin spellings that vary between writers but sound the same, applied after
# romanisation so "kohomada", "kohamada" and "kohomadha" collapse together.
LATIN_RULES = [
    (r'ph', 'p'), (r'th', 't'), (r'dh', 'd'), (r'kh', 'k'), (r'gh', 'g'),
    (r'bh', 'b'), (r'sh', 's'), (r'ch', 'c'), (r'jh', 'j'), (r'zh', 's'),
    (r'ck', 'k'), (r'qu', 'k'), (r'q', 'k'), (r'x', 'ks'), (r'z', 's'),
    (r'w', 'v'), (r'ee', 'i'), (r'oo', 'u'), (r'ou', 'u'), (r'ae', 'a'),
    (r'ei', 'e'), (r'ie', 'i'), (r'y(?![aeiou])', 'i'),
    # c and k are the same sound here. Whisper writes "Kromn" for "chrome" and "kalkulate"
    # for "calculate", so folding them is what lets an English loanword match.
    (r'c', 'k'),
]

# Letters that are not accented forms and so survive NFD unchanged. Dropping them silently
# turned Whisper's Icelandic "það" into "a".
LATIN_EXTRAS = {
    'þ': 't', 'ð': 'd', 'ø': 'o', 'æ': 'a', 'œ': 'o', 'ß': 's', 'ł': 'l', 'đ': 'd',
    'ħ': 'h', 'ŋ': 'n', 'ı': 'i', 'ŧ': 't', 'ơ': 'o', 'ư': 'u', 'ə': 'a', 'ɛ': 'e',
}


def _tables():
    """One consonant / vowel / sign table covering every script Whisper reaches for.

    Indic scripts all work the same way: a bare consonant carries an inherent 'a', a
    following vowel sign replaces it, and a virama removes it. Handling them with one
    table is both shorter and more correct than special-casing each script.
    """
    consonants = dict(CONSONANTS)
    vowels = dict(VOWELS)
    signs = dict(SIGNS)
    for source in (TAMIL, TELUGU):
        consonants.update(source)
    for source in (TAMIL_VOWELS, TELUGU_VOWELS):
        vowels.update(source)
    for source in (TAMIL_SIGNS, TELUGU_SIGNS):
        signs.update(source)
    # The legacy flat table: a value in 'aeiou' is a vowel or sign, anything else a
    # consonant, and an empty value is that script's virama.
    for character, sound in OTHER_INDIC.items():
        if not sound:
            continue
        if sound in ('a', 'i', 'u', 'e', 'o', 'ai', 'au'):
            (vowels if character.isalpha() and not unicodedata.combining(character)
             else signs)[character] = sound
        else:
            consonants.setdefault(character, sound)
    return consonants, vowels, signs


CONSONANT_TABLE, VOWEL_TABLE, SIGN_TABLE = _tables()


def romanize(text):
    """Reduce any script Whisper produced to a rough Latin phonetic form."""
    if not text:
        return ''
    text = unicodedata.normalize('NFC', str(text))
    out = []
    index = 0
    while index < len(text):
        character = text[index]
        if character in CONSONANT_TABLE:
            sound = CONSONANT_TABLE[character]
            following = text[index + 1] if index + 1 < len(text) else ''
            if following in VIRAMAS:
                out.append(sound)
                index += 2
                continue
            if following in SIGN_TABLE:
                out.append(sound + SIGN_TABLE[following])
                index += 2
                continue
            out.append(sound + 'a')          # inherent vowel
            index += 1
            continue
        if character in VOWEL_TABLE:
            out.append(VOWEL_TABLE[character])
        elif character in SIGN_TABLE:
            out.append(SIGN_TABLE[character])
        elif character in (ANUSVARA, VISARGA):
            out.append('n' if character == ANUSVARA else 'h')
        elif character in VIRAMAS:
            pass
        elif character.isascii() and (character.isalpha() or character.isdigit()):
            # Digits are kept: "remind me in 5 minutes" and "calculate 250 * 4" both
            # depend on them, and dropping them silently loses the number.
            out.append(character.lower())
        elif character.lower() in LATIN_EXTRAS:
            out.append(LATIN_EXTRAS[character.lower()])
        elif character.isalpha():
            # Accented Latin. Whisper labels Sinhala audio Swedish, Icelandic or Spanish
            # and writes "Baríganegu lókkarann"; dropping the accented letters outright
            # left "barganegu lkaran" and misrouted the command. Strip the diacritic and
            # keep the letter.
            stripped = ''.join(part for part in unicodedata.normalize('NFD', character)
                               if not unicodedata.combining(part))
            if stripped.isascii() and stripped.isalpha():
                out.append(stripped.lower())
        elif character.isspace():
            out.append(' ')
        index += 1
    return ' '.join(''.join(out).split())


def normalize(text):
    """Romanise, then fold spelling variants, so two spellings of a sound match."""
    result = romanize(text).lower()
    for pattern, replacement in LATIN_RULES:
        result = re.sub(pattern, replacement, result)
    result = re.sub(r'([a-z])\1+', r'\1', result)    # collapse doubled letters
    result = re.sub(r'[^a-z0-9 ]+', '', result)
    return ' '.join(result.split())


MIN_LENGTH = 5      # shorter than any real command, once spaces are removed


def similarity(left, right):
    """0..1 phonetic closeness of two strings in any script.

    Length-aware on purpose. A plain sequence ratio is badly inflated for short strings:
    the four letters "hana" scored 0.47 against a full command and "Thank you." scored
    0.48, both above the acceptance floor, which would have fired a real action on noise.
    Scaling by how much of the longer string is actually covered removes that.
    """
    from difflib import SequenceMatcher
    a, b = normalize(left).replace(' ', ''), normalize(right).replace(' ', '')
    if not a or not b or len(a) < MIN_LENGTH:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    coverage = min(len(a), len(b)) / max(len(a), len(b))
    return ratio * (.5 + .5 * coverage)


def contains(haystack, needle):
    """Is the needle present phonetically, allowing for recogniser noise?"""
    a, b = normalize(haystack).replace(' ', ''), normalize(needle).replace(' ', '')
    if not a or not b:
        return False
    if b in a:
        return True
    from difflib import SequenceMatcher
    window = len(b)
    for start in range(0, max(1, len(a) - window + 1)):
        if SequenceMatcher(None, a[start:start + window], b).ratio() >= .8:
            return True
    return False
