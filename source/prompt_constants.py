"""Shared prompt constants for theme and culture across AI-assist and generation.

These dictionaries provide two views of the same themes and cultures:
- ``*_SCENARIO_PROMPTS`` are used by the AI-assist endpoints when building
  scenarios (names, personalities, events, arcs).  They are more descriptive.
- ``*_GENERATION_HINTS`` are used at generation time when the LLM produces
  actual message content.  They are terse and tone-focused.

Both views share the same key sets so that a theme or culture selected in the
UI is valid everywhere.
"""

# ---------------------------------------------------------------------------
# Theme prompts — scenario building (AI assist)
# ---------------------------------------------------------------------------

THEME_SCENARIO_PROMPTS: dict[str, str] = {
    "slice-of-life": "The scenario is a realistic slice-of-life drama. Characters have everyday jobs, relationships, and social lives.",
    "crime": (
        "The scenario is a crime / investigation story. Characters may include "
        "detectives, suspects, informants, lawyers, and witnesses. Events involve "
        "stakeouts, interrogations, evidence, cover-ups, and court proceedings."
    ),
    "espionage": (
        "The scenario is a spy / espionage thriller. Characters may include "
        "intelligence officers, handlers, assets, double agents, and diplomats. "
        "Events involve dead drops, coded messages, surveillance, safe houses, "
        "border crossings, and covert operations."
    ),
    "romance": (
        "The scenario is a romance / drama. Characters are caught in love "
        "triangles, first dates, breakups, rekindled relationships, and "
        "passionate encounters. Events center on dates, surprises, "
        "misunderstandings, and heartfelt confessions."
    ),
    "family-drama": (
        "The scenario is a family drama. Characters are relatives dealing "
        "with inheritance disputes, holiday gatherings, generational conflict, "
        "secrets coming to light, and family loyalty tests."
    ),
    "college": (
        "The scenario is set in college life. Characters are students, "
        "professors, and campus staff. Events involve classes, parties, "
        "exams, dorm drama, student organizations, and coming-of-age moments."
    ),
    "corporate": (
        "The scenario is a corporate / business thriller. Characters are "
        "executives, analysts, whistleblowers, and consultants. Events involve "
        "hostile takeovers, insider trading, office politics, layoffs, and "
        "high-stakes negotiations."
    ),
    "thriller": (
        "The scenario is a suspense / thriller. Characters face escalating "
        "danger, anonymous threats, disappearances, and race-against-time "
        "situations. Events are tense and high-stakes."
    ),
    "comedy": (
        "The scenario is a comedy / sitcom. Characters are quirky, witty, "
        "and often in absurd situations. Events are lighthearted — "
        "misunderstandings, pranks, awkward encounters, and running gags."
    ),
}


# ---------------------------------------------------------------------------
# Theme hints — message generation (terse, tone-focused)
# ---------------------------------------------------------------------------

THEME_GENERATION_HINTS: dict[str, str] = {
    "slice-of-life": "The conversation is everyday, mundane life — food plans, gossip, complaints, jokes.",
    "crime": (
        "The conversation takes place in a crime scenario. Characters may discuss investigations, "
        "alibis, evidence, court dates, legal strategy, or suspicious activity."
    ),
    "espionage": (
        "The conversation is part of an espionage scenario. Characters may use coded language, "
        "discuss 'packages', 'meetings', and 'assets'. Some messages should feel deliberately vague."
    ),
    "romance": (
        "The conversation is in a romance / drama setting. Characters flirt, argue about feelings, "
        "plan dates, deal with jealousy, or confide in friends about relationships."
    ),
    "family-drama": (
        "The conversation is within a family drama. Characters discuss relatives, inheritance, "
        "holiday planning, grudges, and family obligations."
    ),
    "college": (
        "The conversation is between college students/staff. Discuss classes, parties, study groups, "
        "campus events, dorm life, and the chaos of young adult life."
    ),
    "corporate": (
        "The conversation is in a corporate setting. Characters discuss deals, office politics, "
        "presentations, layoff rumors, after-work drinks, and career moves."
    ),
    "thriller": (
        "The conversation is in a thriller/suspense setting. Characters face escalating tension, "
        "receive cryptic warnings, discuss disappearances, and share urgent information."
    ),
    "comedy": (
        "The conversation is in a comedic setting. Characters banter, riff on absurd situations, "
        "send memes, use running jokes, and react to ridiculous everyday scenarios."
    ),
}


# ---------------------------------------------------------------------------
# Culture prompts — scenario building (AI assist)
# Setting-only: describes the world, NOT character demographics.
# ---------------------------------------------------------------------------

CULTURE_SCENARIO_PROMPTS: dict[str, str] = {
    "american": (
        "The scenario is set in the United States. Daily life references include "
        "American cities, food chains, sports, pop culture, and holidays "
        "(Thanksgiving, 4th of July). The cast may include people of ANY ethnic "
        "or cultural background — generate a diverse, realistic mix."
    ),
    "arab-gulf": (
        "The scenario is set in the Gulf Arab region (UAE, Saudi Arabia, Qatar, Kuwait, "
        "Bahrain, Oman). Daily life references include majlis, shisha cafes, desert trips, "
        "malls, Ramadan, Eid celebrations, and local food. The cast may include expats, "
        "migrant workers, and visitors alongside locals — generate a realistic mix."
    ),
    "arab-levant": (
        "The scenario is set in the Levant (Lebanon, Syria, Jordan, Palestine). References "
        "include Levantine food, family gatherings, Ramadan traditions, university life, "
        "and Mediterranean culture. The cast may include diverse backgrounds."
    ),
    "arab-north-africa": (
        "The scenario is set in North Africa (Egypt, Morocco, Tunisia, Algeria, Libya). "
        "References include local cuisine (koshari, tagine, couscous), souks, tea culture, "
        "and religious holidays. The cast may include diverse backgrounds."
    ),
    "british": (
        "The scenario is set in the United Kingdom. References include pubs, football, "
        "the NHS, uni life, high street shops, and London neighborhoods or Northern cities. "
        "The cast may include people of any heritage — Britain is multicultural."
    ),
    "chinese": (
        "The scenario is set in China. References include WeChat culture, hotpot, KTV, "
        "Spring Festival, Mid-Autumn Festival, bubble tea, and regional food. "
        "The cast may include expats, foreign students, and visitors alongside locals."
    ),
    "french": (
        "The scenario is set in France. References include cafes, boulangeries, apéro "
        "culture, French cinema, and regional pride. The cast may include people of any "
        "background — France is multicultural."
    ),
    "indian": (
        "The scenario is set in India. References include chai, cricket, Bollywood, "
        "festivals (Diwali, Holi, Eid), street food, family dynamics, and regional "
        "variations. The cast may include diverse backgrounds."
    ),
    "japanese": (
        "The scenario is set in Japan. References include konbini culture, izakaya, train "
        "commutes, hanami, matsuri, and seasonal food. The cast may include expats and "
        "visitors alongside locals."
    ),
    "korean": (
        "The scenario is set in South Korea. References include PC bangs, soju culture, "
        "K-pop, delivery food, Chuseok, and Lunar New Year. The cast may include "
        "diverse backgrounds."
    ),
    "latin-american": (
        "The scenario is set in Latin America. References include local food, telenovelas, "
        "fútbol, quinceañeras, family gatherings, and regional slang. The cast may include "
        "people of any background."
    ),
    "nigerian": (
        "The scenario is set in Nigeria. References include jollof rice debates, Nollywood, "
        "owambe parties, danfo buses, generator culture, and Pidgin English expressions. "
        "The cast may include diverse backgrounds."
    ),
    "russian": (
        "The scenario is set in Russia. References include dachas, banya, tea culture, "
        "New Year celebrations, Victory Day, blini, pelmeni, and the metro. The cast may "
        "include expats and visitors alongside locals."
    ),
    "southeast-asian": (
        "The scenario is set in Southeast Asia (Philippines, Thailand, Vietnam, Indonesia, "
        "Malaysia). References include street food markets, karaoke, monsoon season, "
        "motorbike culture, and regional festivals. The cast may be diverse."
    ),
    "turkish": (
        "The scenario is set in Turkey. References include çay culture, bazaars, kebab, "
        "Turkish breakfast, Ramadan, bayram holidays, and Istanbul neighborhoods. "
        "The cast may include diverse backgrounds."
    ),
    "west-african": (
        "The scenario is set in West Africa (Ghana, Senegal, Cameroon, Côte d'Ivoire). "
        "References include fufu, jollof, market days, church/mosque, family obligations, "
        "and local music scenes. The cast may include diverse backgrounds."
    ),
}


# ---------------------------------------------------------------------------
# Culture hints — message generation (terse, setting-focused)
# ---------------------------------------------------------------------------

CULTURE_GENERATION_HINTS: dict[str, str] = {
    "american": (
        "The story is set in the United States. Local details like neighborhoods, stores, "
        "holidays (Thanksgiving, 4th of July), and commute patterns may appear when natural. "
        "The cast may include people of ANY background — the setting is multicultural."
    ),
    "arab-gulf": (
        "The story is set in the Gulf region (UAE/Saudi/Qatar). Local life details — malls, "
        "shisha cafes, desert trips, Ramadan, Eid — may surface naturally. The cast may "
        "include expats, migrant workers, and visitors alongside locals."
    ),
    "arab-levant": (
        "The story is set in the Levant (Lebanon/Syria/Jordan/Palestine). Local context — "
        "food, family gatherings, Mediterranean atmosphere — grounds the setting. "
        "The cast may include diverse backgrounds living in the region."
    ),
    "arab-north-africa": (
        "The story is set in North Africa (Egypt/Morocco/Tunisia/Algeria). Everyday details "
        "like souks, tea culture, and regional cuisine flavor the setting. Characters may "
        "come from any ethnic background."
    ),
    "british": (
        "The story is set in the UK. Local details — pubs, football, the NHS, high street "
        "shops — anchor the setting. The cast may include people of any heritage."
    ),
    "chinese": (
        "The story is set in China. Daily-life details like city transit, local food, "
        "apps (WeChat), and festivals anchor the setting. The cast may include "
        "expats, foreign students, and visitors alongside locals."
    ),
    "french": (
        "The story is set in France. Cafés, boulangeries, apéro culture, and regional "
        "pride flavor the setting. Characters may come from any background."
    ),
    "indian": (
        "The story is set in India. Diverse regional details — festivals, street food, "
        "cricket, family dynamics — ground the setting. The cast may include people of "
        "any heritage living in or visiting the region."
    ),
    "japanese": (
        "The story is set in Japan. Konbini, trains, izakaya, seasonal events anchor "
        "the setting. The cast may include expats and visitors alongside locals."
    ),
    "korean": (
        "The story is set in South Korea. PC bangs, soju culture, delivery food, and "
        "seasonal events flavor the setting. The cast may be diverse."
    ),
    "latin-american": (
        "The story is set in Latin America. Local food, fútbol, family gatherings, and "
        "regional slang ground the setting. Characters may come from any heritage."
    ),
    "nigerian": (
        "The story is set in Nigeria. Everyday context — generator culture, danfo, "
        "owambe parties, local food debates — anchors the setting. The cast may include "
        "diverse backgrounds."
    ),
    "russian": (
        "The story is set in Russia. Dachas, banya, tea culture, and metro commutes "
        "flavor the setting. Characters may include expats alongside locals."
    ),
    "southeast-asian": (
        "The story is set in Southeast Asia. Street food markets, monsoon seasons, "
        "motorbike culture, and local festivals ground the setting. The cast may "
        "include diverse backgrounds."
    ),
    "turkish": (
        "The story is set in Turkey. Çay, bazaars, Turkish breakfast, and bayram "
        "holidays flavor the setting. Characters may come from any background."
    ),
    "west-african": (
        "The story is set in West Africa. Market days, local music, family obligations, "
        "and regional food anchor the setting. The cast may be diverse."
    ),
}
