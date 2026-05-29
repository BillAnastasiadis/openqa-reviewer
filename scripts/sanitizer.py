import re
import hashlib
import os

try:
    import enchant
    english_dict = enchant.Dict("en_US")
except ImportError:
    raise ImportError("The 'pyenchant' library is required. Please run: pip install pyenchant")

# prepend 16 random byts perr run to prevent rainbow-table/dictionary attacks
SALT = os.urandom(16).hex().encode("utf-8")

# More specific regexes should be placed above general ones.
REPLACEMENTS = [
    (re.compile(r'()(\bINTERNAL-USE-ONLY-[a-zA-Z0-9]+\b)()'), 'REDACTED_INTERNAL'),
    (re.compile(r'(?i)(\b(?:password|passwd|pwd|secret|client_secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*)([^,\s;\'"&)]+)()'), 'REDACTED_SECRET'),
    (re.compile(r'(?i)(\bauthorization\s*:\s*(?:bearer|basic|token)\s+)([^\s]+)()'), 'REDACTED_AUTH'),
    (re.compile(r'(?i)([\'"](?:password|passwd|pwd|secret|token|api[_-]?key|client_secret|access_token|refresh_token)[\'"]\s*:\s*[\'"])([^\'"]*)([\'"])'), 'REDACTED_JSON_VALUE'),
    (re.compile(r'(?i)(\b(?:password|secret|token|api_key)\s*:\s*)([^\s]+)()'), 'REDACTED_YAML_VALUE'),
    (re.compile(r'(?i)(\b(?:postgres|mysql|mongodb|redis)://[a-zA-Z0-9_-]+:)([^@\s]+)(@)'), 'REDACTED_DB_CREDENTIAL'),
    (re.compile(r'(https?://[a-zA-Z0-9_-]+:)([^@\s]+)(@)'), 'REDACTED_BASIC_AUTH'),
    (re.compile(r'(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)'), 'REDACTED_URL_CREDENTIAL'),
    (re.compile(r'()(\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9._-]+\.[a-zA-Z0-9._-]+\b)()'), 'REDACTED_JWT'),
    (re.compile(r'()(\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b)()'), 'REDACTED_AWS_KEY'),
    (re.compile(r'()(\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}\b)()'), 'REDACTED_GITHUB_TOKEN'),
    (re.compile(r'()(\b[rs]k_(?:test|live)_[0-9a-zA-Z]{24}\b)()'), 'REDACTED_STRIPE_KEY'),
    (re.compile(r'()(\bxox[baprs]-[0-9]{10,13}-[a-zA-Z0-9]{24}\b)()'), 'REDACTED_SLACK_TOKEN'),
    (re.compile(r'()(https://hooks\.slack\.com/services/T[a-zA-Z0-9_]+/B[a-zA-Z0-9_]+/[a-zA-Z0-9_]+)()'), 'REDACTED_SLACK_WEBHOOK'),
    (re.compile(r'()(\bAIza[0-9A-Za-z\-_]{35}\b)()'), 'REDACTED_GCP_API_KEY'),
    (re.compile(r'()(\bya29\.[0-9A-Za-z_-]+\b)()'), 'REDACTED_GCP_OAUTH'),
    (re.compile(r'()(\bSG\.[0-9A-Za-z_-]{22}\.[0-9A-Za-z_-]{43}\b)()'), 'REDACTED_SENDGRID_KEY'),
    (re.compile(r'()(\bSK[0-9a-fA-F]{32}\b)()'), 'REDACTED_TWILIO_KEY'),
    (re.compile(r'()(\bsq0atp-[0-9A-Za-z\-_]{22}\b)()'), 'REDACTED_SQUARE_TOKEN'),
    (re.compile(r'()(\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b)()'), 'REDACTED_UUID'),
    (re.compile(r'(?i)(\b(?:account[_-]?id|aws[_-]?account|subscription[_-]?id)[\'"]?\s*[:=]\s*[\'"]?)(\d{12})()'), 'REDACTED_AWS_ACCOUNT_ID'),
    (re.compile(r'()(\b(?:[3456]\d{3}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}|\d{4}[-\s]?\d{6}[-\s]?\d{5})\b)()'), 'REDACTED_CREDIT_CARD'),
    (re.compile(r'()(\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b)()'), 'REDACTED_EMAIL'),
    (re.compile(r'()(\b[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+\b)()'), 'REDACTED_USER_HOST'),
    (re.compile(r'()(https?://[^\s"\'<>\[\]]+)()'), 'REDACTED_URL'),
    (re.compile(r'()(\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b)()'), 'REDACTED_MAC'), 
    (re.compile(r'()(\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b|(?:[A-Fa-f0-9]{1,4}:){1,7}:|:(?::[A-Fa-f0-9]{1,4}){1,7})()'), 'REDACTED_IPv6'),
    (re.compile(r'()(\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b)()'), 'REDACTED_IPv4'),
    
    # Aggressive additions
    (re.compile(r'()(\bssh-(?:rsa|ed25519|dss)\s+[A-Za-z0-9+/]+[=]{0,2}\b)()'), 'REDACTED_SSH_PUB_KEY'),
    (re.compile(r'()(\b(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9+/=_-]{16,}\b)()'), 'REDACTED_HIGH_ENTROPY'),
    (re.compile(r'()((?:~)?(?:/[a-zA-Z0-9_.-]+){2,})()'), 'REDACTED_PATH'),
    (re.compile(r'()([a-zA-Z]:\\(?:[a-zA-Z0-9_.-]+\\){1,}[a-zA-Z0-9_.-]+)()'), 'REDACTED_WIN_PATH'),
    (re.compile(r'()(\b[a-fA-F0-9]{8,}\b)()'), 'REDACTED_HEX_STRING'),
    (re.compile(r'(?i)([\'"](?:admin_user|username|user|deployment_name|project_tag|project)[\'"]\s*:\s*[\'"])([^\'"]+)([\'"])'), 'REDACTED_CONFIG_VALUE'),
    (re.compile(r'()(\b(?=[a-z]*[0-9])(?=[0-9]*[a-z])[a-z0-9]{12,}\b)()'), 'REDACTED_ALPHANUM_ID'),
    (re.compile(r'()(\b(?:us|eu|ap|sa|ca|me|af)-(?:north|south|east|west|central|northeast|southeast)-\d\b)()'), 'REDACTED_AWS_REGION'),
    (re.compile(r'()(\b(?:t2|t3|t4g|m5|m6g|c5|c6g|r5|r5b|r6g|i3|p3|p4|g4dn|mac1)\.[a-zA-Z0-9]+\b)()'), 'REDACTED_INSTANCE_TYPE'),
]

DOMAIN_DICTIONARY = {
    # SUSE/SAP/openQA/etc stuff
    'suse', 'opensuse', 'openqa', 'autoinst', 'osad', 'zypper', 'yast', 'leap', 'sle', 'sles', 'btrfs',
    'hugepage', 'saptune', 'hana', 'guestregister',
    # General IT stuff
    'localhost', 'nginx', 'apache', 'docker', 'kubernetes', 'repo', 'config', 'api', 'http', 'https',
    'json', 'yaml', 'xml', 'html', 'sql', 'db', 'ssh', 'ssl', 'tls', 'tcp', 'udp', 'ip', 'ipv4', 'ipv6',
    'linux', 'ubuntu', 'debian', 'centos', 'redhat', 'fedora', 'mac', 'windows', 'vm', 'hypervisor',
    'backend', 'frontend', 'middleware', 'auth', 'oauth', 'jwt', 'token', 'admin', 'root', 'user',
    'tmp', 'dev', 'sys', 'opt', 'usr', 'var', 'etc', 'bin', 'lib', 'src', 'dest', 'dir', 'file',
    'sync', 'async', 'init', 'exec', 'pid', 'uid', 'gid', 'stdout', 'stderr', 'stdin', 'log', 'debug',
    'info', 'warn', 'error', 'fatal', 'trace', 'env', 'pwd', 'cmd', 'cli', 'gui', 'ui', 'ux', 'git',
    'commit', 'push', 'pull', 'merge', 'branch', 'main', 'master', 'pr', 'issue', 'bug', 'fix', 'libsolv',
    'libzypp'
}

def get_hash(secret):
    """Returns a salted hash to prevent dictionary attacks on the redacted data."""
    return hashlib.sha256(SALT + secret.encode("utf-8")).hexdigest()[:6]

def is_valid_word(word):
    """Checks if a word is in the domain list or the English dictionary."""
    clean_word = re.sub(r'[^a-zA-Z]', '', word).lower()
    if not clean_word or len(clean_word) < 2:
        return True 
        
    if clean_word in DOMAIN_DICTIONARY:
        return True
        
    return english_dict.check(clean_word)

def smart_split(token):
    """Splits snake_case, kebab-case, and CamelCase"""
    parts = re.split(r'[_-]', token)
    final_parts = []
    
    for part in parts:
        # CamelCase splitter - some errors use CamelCase
        camel_parts = re.finditer(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+', part)
        extracted = [m.group(0) for m in camel_parts]
        
        if extracted:
            final_parts.extend(extracted)
        else:
            final_parts.append(part)
            
    return [p for p in final_parts if p]

def evaluate_token(token):
    """Decides whether to keep, partially redact, or fully hash a token."""
    
    if len(token) <= 4:
        return token
        
    if is_valid_word(token):
        return token

    # if it's a package, keep it
    rpm_pattern = re.compile(
        r'^[a-zA-Z0-9_+-]+'
        r'-\d[a-zA-Z0-9_.~+]*'
        r'-[a-zA-Z0-9_.~+]+'
        r'\.(?:x86_64|noarch|aarch64|s390x|ppc64le|i[3456]86|src|nosrc)$'
    )
    if rpm_pattern.match(token):
        return token
        
    sub_words = smart_split(token)
    
    sensitive_terms = {'secret', 'token', 'key', 'pass', 'pwd', 'credential', 'cert', 'auth'}
    if any(w.lower() in sensitive_terms for w in sub_words):
         return f"[REDACTED_SENSITIVE_{get_hash(token)}]"

    valid_count = sum(1 for w in sub_words if is_valid_word(w))
    
    # Redact if it's total gibberish (0 valid words) + long length
    if valid_count == 0 and len(token) > 14:
        return f"[REDACTED_LONG_UNKNOWN_{get_hash(token)}]"
        
    # Mix of english and gibberish - keep english
    if valid_count > 0 and valid_count < len(sub_words):
        def inline_replacer(match):
            chunk = match.group(0)
            if is_valid_word(chunk) or len(chunk) <= 4 or chunk.isdigit():
                return chunk
            return f"[REDACTED_{get_hash(chunk)}]"
            
        # Rebuild the token
        rebuilt_token = re.sub(r'[a-zA-Z0-9]+', inline_replacer, token)
        return rebuilt_token

    # Keep if all words are valid English
    if valid_count == len(sub_words):
        return token

    # check randomness for short, unknown strings
    vowels = sum(1 for c in token.lower() if c in 'aeiou')
    digits = sum(1 for c in token if c.isdigit())
    
    if digits / len(token) > 0.3 or (len(token) > 7 and vowels / len(token) < 0.15):
        return f"[REDACTED_SHORT_GIBBERISH_{get_hash(token)}]"

    return token


def sanitize_line(line, trace=False):
    """Applies strict regexes, then heuristically tokenizes the rest."""
    
    raw_suffix = ""
    safe_trace_suffix = ""
    
    # Only isolate perl traces
    if trace:
        trace_pattern = re.compile(r'( at\s+[/\w.-]+\.pm\s+line\s+\d+.*$)')
        match = trace_pattern.search(line)
        if match:
            safe_trace_suffix = match.group(1)
            # remove from line temporarily
            line = line.replace(safe_trace_suffix, " __SAFE_TRACE_MARKER__ ")
    
    # If tracing is enabled, isolate everything after "(called) at " to bypass sanitization
    if trace and " at " in line:
        parts = line.split(" at ", 1)
        line = parts[0] + " at "
        raw_suffix = parts[1]
    
    # Known regexes
    for pattern, label in REPLACEMENTS:
        def regex_replacer(match):
            prefix = match.group(1)
            secret = match.group(2)
            suffix = match.group(3)
            secret_hash = get_hash(secret)
            return f"{prefix}[{label}_{secret_hash}]{suffix}"
        line = pattern.sub(regex_replacer, line)

    # Tokenize the rest by brackets/punctuation
    tokens = re.split(r'(\s+|[\[\]{}()\'"=:;,<>|])', line)
    
    sanitized_tokens = []
    for token in tokens:
        if token.strip() and not re.match(r'^[\[\]{}()\'"=:;,<>|]+$', token):
            # Skip if already REDACTED
            if "REDACTED_" in token:
                sanitized_tokens.append(token)
            else:
                sanitized_tokens.append(evaluate_token(token))
        else:
            sanitized_tokens.append(token)
            
    final_sanitized_line = "".join(sanitized_tokens)
    
    # 4. Restore matched trace
    if safe_trace_suffix:
        final_sanitized_line = final_sanitized_line.replace(" __SAFE_TRACE_MARKER__ ", safe_trace_suffix)
        
    return final_sanitized_line

def sanitize_text(text, trace=False):
    """Processes a multi-line string, applying PEM checks and targeted sanitization."""
    lines = text.splitlines()
    sanitized_lines = []
    in_pem_block = False

    for line in lines:
        if re.search(r'-----BEGIN .*KEY-----|-----BEGIN .*PRIVATE KEY BLOCK-----|-----BEGIN .*CERTIFICATE-----', line):
            in_pem_block = True
            sanitized_lines.append("[REDACTED_KEY_BLOCK]")
            continue
        
        if in_pem_block:
            if re.search(r'-----END .*KEY-----|-----END .*PRIVATE KEY BLOCK-----|-----END .*CERTIFICATE-----', line):
                in_pem_block = False
            continue

        sanitized_lines.append(sanitize_line(line, trace=trace))

    return "\n".join(sanitized_lines)

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Sanitize logs and text outputs.")
    
    parser.add_argument("infile", nargs="?", type=argparse.FileType("r"), default=sys.stdin, help="File to sanitize (defaults to stdin)")
    
    parser.add_argument("--trace", action="store_true", help=argparse.SUPPRESS)
    
    args = parser.parse_args()
    
    input_text = args.infile.read()
    output_text = sanitize_text(input_text, trace=args.trace)
    
    print(output_text)