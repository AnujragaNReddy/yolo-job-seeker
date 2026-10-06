"""Finding skills in free text.

A curated vocabulary with aliases, matched on word boundaries. Not a model,
deliberately: the result has to be explainable. When the UI says a job matched
at 82%, it has to be able to list which skills that came from, and a scorer
nobody can audit is worse than useless for deciding where to spend an
afternoon applying.

The matching problems here are all about boundaries rather than cleverness.
Searching for "r" or "go" as substrings finds them in almost every document
ever written, and "react" appears inside "reactive". So every term is matched
as a whole word, and a few short ones carry stricter rules.
"""

import re
from typing import Iterable, Optional

# canonical -> the spellings that mean it. The canonical name is what the UI
# shows and what gets stored, so it is the readable one.
TAXONOMY: dict[str, list[str]] = {
    # languages
    "Python": ["python", "python3"],
    "JavaScript": ["javascript", "js", "es6", "ecmascript"],
    "TypeScript": ["typescript", "ts"],
    "Java": ["java"],
    "C#": ["c#", "csharp", "c sharp", ".net", "dotnet"],
    "C++": ["c++", "cpp"],
    "C": ["c language", "ansi c"],
    "Go": ["golang", "go lang"],
    "Rust": ["rust"],
    "Ruby": ["ruby"],
    "PHP": ["php"],
    "Swift": ["swift"],
    "Kotlin": ["kotlin"],
    "Scala": ["scala"],
    "R": ["r language", "rstats"],
    "SQL": ["sql"],
    "Bash": ["bash", "shell scripting", "shell script"],
    "PowerShell": ["powershell"],

    # frontend
    "React": ["react", "react.js", "reactjs"],
    "Next.js": ["next.js", "nextjs"],
    "Vue": ["vue", "vue.js", "vuejs"],
    "Angular": ["angular", "angularjs"],
    "Svelte": ["svelte", "sveltekit"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3"],
    "Tailwind CSS": ["tailwind", "tailwindcss"],
    "SASS": ["sass", "scss"],
    "Redux": ["redux"],
    "Webpack": ["webpack"],
    "Vite": ["vite"],
    "jQuery": ["jquery"],
    "React Native": ["react native"],
    "Flutter": ["flutter"],

    # backend and APIs
    "Node.js": ["node.js", "nodejs", "node"],
    "Express": ["express", "express.js", "expressjs"],
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Spring Boot": ["spring boot", "springboot", "spring"],
    "Rails": ["rails", "ruby on rails"],
    "Laravel": ["laravel"],
    "GraphQL": ["graphql"],
    "REST APIs": ["rest", "restful", "rest api", "rest apis"],
    "gRPC": ["grpc"],
    "Microservices": ["microservices", "microservice"],

    # data stores
    "PostgreSQL": ["postgresql", "postgres", "psql"],
    "MySQL": ["mysql", "mariadb"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch", "elastic search", "opensearch"],
    "SQLite": ["sqlite"],
    "Oracle": ["oracle db", "oracle database", "pl/sql", "plsql"],
    "SQL Server": ["sql server", "mssql", "t-sql", "tsql"],
    "DynamoDB": ["dynamodb"],
    "Cassandra": ["cassandra"],
    "Snowflake": ["snowflake"],
    "BigQuery": ["bigquery", "big query"],

    # cloud and infrastructure
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "Azure": ["azure"],
    "Google Cloud": ["gcp", "google cloud"],
    "Docker": ["docker", "containerisation", "containerization"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform"],
    "Ansible": ["ansible"],
    "Jenkins": ["jenkins"],
    "GitHub Actions": ["github actions"],
    "GitLab CI": ["gitlab ci", "gitlab-ci"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "continuous delivery"],
    "Linux": ["linux", "unix", "ubuntu", "centos"],
    "Nginx": ["nginx"],
    "Kafka": ["kafka"],
    "RabbitMQ": ["rabbitmq"],
    "Serverless": ["serverless"],

    # data and ML
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "PyTorch": ["pytorch", "torch"],
    "TensorFlow": ["tensorflow"],
    "Spark": ["spark", "pyspark", "apache spark"],
    "Airflow": ["airflow"],
    "dbt": ["dbt"],
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "Machine Learning": ["machine learning", "ml models"],
    "Deep Learning": ["deep learning", "neural networks"],
    "NLP": ["nlp", "natural language processing"],
    "Computer Vision": ["computer vision", "opencv"],
    "LLMs": ["llm", "llms", "large language model", "large language models",
             "generative ai", "genai", "prompt engineering", "rag"],
    "Data Engineering": ["data engineering", "etl", "elt", "data pipelines"],
    "Data Analysis": ["data analysis", "data analytics"],

    # practice and tooling
    "Git": ["git", "version control"],
    "Agile": ["agile", "scrum", "kanban"],
    "Testing": ["unit testing", "unit tests", "pytest", "jest", "junit",
                "test automation", "tdd"],
    "Selenium": ["selenium"],
    "Cypress": ["cypress"],
    "Playwright": ["playwright"],
    "JIRA": ["jira"],
    "System Design": ["system design", "distributed systems", "architecture"],
    "Security": ["owasp", "penetration testing", "appsec",
                 "application security", "cybersecurity"],
    "Observability": ["observability", "prometheus", "grafana", "datadog",
                      "monitoring"],

    # non-engineering, so a non-technical resume is not read as empty
    "Project Management": ["project management", "program management", "pmp"],
    "Product Management": ["product management", "product owner", "roadmap"],
    "Stakeholder Management": ["stakeholder management", "stakeholder engagement"],
    "Communication": ["written communication", "verbal communication",
                      "presentation skills"],
    "Leadership": ["team leadership", "people management", "mentoring",
                   "line management"],
    "Excel": ["excel", "advanced excel", "vlookup", "pivot tables"],
    "Salesforce": ["salesforce"],
    "SAP": ["sap"],
    "Accounting": ["accounting", "bookkeeping", "reconciliation"],
    "Customer Support": ["customer support", "customer service", "helpdesk"],
    "Recruiting": ["recruiting", "recruitment", "talent acquisition"],
    "Content Writing": ["content writing", "copywriting", "technical writing"],
    "SEO": ["seo", "search engine optimisation", "search engine optimization"],
    "Figma": ["figma"],
    "UX Design": ["ux design", "user experience", "ui/ux", "wireframing"],
}

# Terms this short match far too much by accident, so they need their own
# spelling to count rather than the bare letter.
_NEVER_BARE = {"r", "c", "go", "ts", "js", "ml", "ai"}

# Built once: regex compilation is not free and this runs over every posting.
_PATTERNS: list[tuple[str, re.Pattern]] = []

#  Spaces, hyphens, slashes and underscores are all the same separator in
#  practice: "ci/cd", "ci-cd", "CI CD" and "cicd" are one skill written four
#  ways. So a multi-word alias matches across any of them, or none.
#
#  Dots are NOT in that set. Splitting on them would turn ".net" into a
#  pattern for a bare "net", which matches "network" and "net new revenue".
_SEPARATOR = r"[\s\-/_]*"
_SPLIT_ON = re.compile(r"[\s\-/_]+")

for canonical, aliases in TAXONOMY.items():
    for alias in {canonical.lower(), *aliases}:
        if alias in _NEVER_BARE:
            continue

        body = _SEPARATOR.join(
            re.escape(part) for part in _SPLIT_ON.split(alias) if part
        )

        # \b does not work beside +, # or . — "c++" ends in punctuation, so a
        # trailing \b would demand a word character that is never there.
        left = r"(?<![A-Za-z0-9+#])"
        right = r"(?![A-Za-z0-9+#])"
        _PATTERNS.append((canonical, re.compile(left + body + right, re.IGNORECASE)))


def extract(text: Optional[str]) -> list[str]:
    """Canonical skill names present in the text, in taxonomy order."""
    if not text:
        return []

    # Hyphens and slashes join words that should be found separately
    # ("python/django"), while keeping +, # and . which belong to names.
    haystack = re.sub(r"[\-/_,;:|()\[\]]+", " ", text)
    found = []

    for canonical, pattern in _PATTERNS:
        if canonical in found:
            continue
        if pattern.search(haystack):
            found.append(canonical)

    return found


def extract_counts(text: Optional[str]) -> dict[str, int]:
    """How often each skill appears.

    A posting that names a skill repeatedly usually means it, which is worth
    more than one buried in a wish list at the bottom.
    """
    if not text:
        return {}

    haystack = re.sub(r"[\-/_,;:|()\[\]]+", " ", text)
    counts: dict[str, int] = {}

    for canonical, pattern in _PATTERNS:
        hits = len(pattern.findall(haystack))
        if hits:
            counts[canonical] = counts.get(canonical, 0) + hits

    return counts


def known(names: Iterable[str]) -> list[str]:
    """Keep only names in the taxonomy, canonicalising case.

    Used on anything a person or a model supplied, so a typo cannot quietly
    become a skill nothing will ever match.
    """
    lookup = {name.lower(): name for name in TAXONOMY}
    cleaned = []

    for name in names or []:
        canonical = lookup.get(str(name).strip().lower())
        if canonical and canonical not in cleaned:
            cleaned.append(canonical)

    return cleaned
