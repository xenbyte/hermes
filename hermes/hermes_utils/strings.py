from hermes_utils.db import get_user_lang
from hermes_utils.meta import LOVE_EMOJI

_STRINGS = {
    "start": {
        "en": r"""Hi there\!

I check real estate websites for new rental homes in The Netherlands\. For more info on which websites I check, say /websites\.

To see and modify your personal filters \(like city and maximum price\), say /filter\.

You will receive a message when I find a new home that matches your filters\! If you want me to stop, just say /stop\.

You can also use the website to manage your filters and see new listings come in live\!

If you have any questions, please read the /faq\!

\(zeg /nl als je mij liever in het Nederlands wilt\)"""
    },
    "already_subscribed": {
        "en": "You are already subscribed, I'll let you know if I see any new rental homes online!",
        "nl": "Je bent al geregistreerd, ik stuur een berichtje als er nieuwe huurwoningen online komen!"
    },
    "stop": {
        "en": rf"""You will no longer receive updates for new listings\. I hope this is because you've found a new home\!
        
Consider [buying me a beer]({{}}) if Hermes has helped you in your search {LOVE_EMOJI}""",
        "nl": rf"""Je ontvangt geen updates meer voor nieuwe woningen\. Ik hoop dat dit is omdat je een nieuw huis hebt gevonden\!

Je kunt eventueel [een biertje voor me kopen]({{}}) als Hermes je heeft geholpen in je zoektocht {LOVE_EMOJI}"""
    },
    
    "websites": {
        "en": "Here are the websites I scrape every five minutes:\n\n",
        "nl": "Dit zijn de websites die ik elke vijf minuten check:\n\n"
    },
    "website_info": {
        "en": "Agency: {}\nWebsite: {}\n\n",
        "nl": "Naam: {}\nWebsite: {}\n\n"
    },
    "source_code": {
        "en": "If you want more information about how I work, I'm open-source: https://github.com/xenbyte/hermes",
        "nl": "Als je meer informatie wilt over hoe ik in elkaar zit, ik ben open-source: https://github.com/xenbyte/hermes"
    },

    "filter" : {
        "en": """*Currently, your filters are:*
Min. price: {}
Max. price: {}
Min. size: {} m\u00b2
Cities: {}

*To change your filters, you can say:*
`/filter minprice 1200`
`/filter maxprice 1800`
`/filter minsqm 40`
`/filter city add Amsterdam`
`/filter city remove Den Haag`
I will only send you homes in cities that you've included in your filter. Say  `/filter city`  to see the list of possible cities.

Additionally, you can disable updates from certain agencies/websites. Say  `/filter agency`  to select your preferences.""",
        "nl": """*Op dit moment zijn jouw filters:*
Min. prijs: {}
Max. prijs: {}
Min. oppervlakte: {} m\u00b2
Steden: {}

*Om je filters aan te passen, zeg je bijvoorbeeld:*
`/filter minprice 1200`
`/filter maxprice 1800`
`/filter minsqm 40`
`/filter city add Amsterdam`
`/filter city remove Den Haag`
Ik stuur alleen meldingen voor woningen in steden die je in je filter hebt opgenomen. Zeg  `/filter city`  om de lijst met mogelijke steden te zien.

Daarnaast kun je updates van bepaalde makelaars/websites uitschakelen. Zeg  `/filter agency`  om deze te selecteren."""
    },
    "filter_minprice": {
        "en": "Your minimum price is now {}",
        "nl": "Je minimumprijs is nu {}"
    },
    "filter_maxprice": {
        "en": "Your maximum price is now {}",
        "nl": "Je maximumprijs is nu {}"
    },
    "filter_minsqm": {
        "en": "Your minimum size is now {} m\u00b2",
        "nl": "Je minimale oppervlakte is nu {} m\u00b2"
    },
    "filter_agency": {
        "en": """Select the agencies you want to receive homes from

A green checkmark means you will receive homes from that agency, a red cross means you won't.""",
        "nl": """Selecteer de makelaars waar je woningen van wilt ontvangen

Een groen vinkje betekent dat je woningen van die makelaar ontvangt, een rood kruisje betekent dat je niets krijgt.""",
    },
    "filter_city_header": {
        "en": "Here are the cities you can add to your filter:\n\n",
        "nl": "Dit zijn de steden die je aan je filter kunt toevoegen:\n\n"
    },
    "filter_city_trailer": {
        "en": "\nThis list is based on the cities I've seen so far while scraping, so it might not be fully complete.",
        "nl": "\nDeze lijst is gebaseerd op de steden die ik tot nu toe ben tegengekomen tijdens het scrapen, dus het kan zijn dat er steden ontbreken."
    },
    "filter_city_invalid": {
        "en": "Invalid city: {}\n\nTo see all possible options, say: `/filter city`",
        "nl": "Ongeldige stad: {}\n\nOm alle opties te zien, zeg: `/filter city`"
    },
    "filter_city_already_in": {
        "en": "{} is already in your filter, so nothing has been changed",
        "nl": "{} staat al in je filter, er is niets aangepast"
    },
    "filter_city_added": {
        "en": "{} added to your city filter",
        "nl": "{} toegevoegd aan je filter voor steden"
    },
    "filter_city_not_in": {
        "en": "{} is not in your filter, nothing has been changed",
        "nl": "{} staat niet in je filter, er is niets aangepast"
    },
    "filter_city_removed": {
        "en": "{} removed from your city filter.",
        "nl": "{} verwijderd uit je stedenfilter"
    },
    "filter_city_empty": {
        "en": "\n\nYour city filter is now empty, you will not receive messages about any homes.",
        "nl": "\n\nJe filter voor steden is nu leeg, je ontvangt geen meldingen voor woningen."
    },
    "filter_invalid_command": {
        "en": "Invalid filter command, say /filter to see options",
        "nl": "Ongeldig filter commando, zeg /filter om de opties te zien"
    },
    "filter_invalid_number": {
        "en": "Invalid value: {} is not a number",
        "nl": "Ongeldige waarde: {} is geen getal"
    },

    "donate": {
        "en": rf"""Moving is expensive enough and similar services start at like €20/month\. Hopefully Hermes has helped you save some money\!
        
You could use some of those savings to [buy me a beer]({{}}) {LOVE_EMOJI}

Good luck in your search\!""",
        "nl": rf"""Verhuizen is al duur genoeg en vergelijkbare diensten kosten minimaal €20/maand\. Hopelijk heeft Hermes je geholpen wat geld te besparen\!

Je kunt een deel van die besparing eventueel gebruiken om [een biertje voor me te kopen]({{}}) {LOVE_EMOJI}

Succes met je zoektocht\!"""
    },

    "faq": {
        "en": rf"""*Why is Hermes free?*
    I built Hermes for myself and once we found a home, I thought it would be nice to share it with others\!

*What websites does Hermes check?*
    Use the command /websites to see the full list\.

*How often does Hermes check the websites?*
    Every 5 minutes\.

*Can you add website \.\.\.?*
    Probably; open a feature request on [GitHub](https://github.com/xenbyte/hermes) or check existing discussions to see if it is already planned\.

*Can you add a filter for: amount of rooms/postal code, etc\.?*
    In short: no, because it makes the service less reliable\. Extra filters increase scraping complexity and failure modes, so we avoid them for now\.

*Does this work if I want to buy a home?*
    Not yet, but who knows what I might build when I\'m looking to buy something myself\!

*I saw this listing on Pararius and I didn\'t get a message from Hermes\. Why?*
    Pararius does not list a house number for all homes, so Hermes can\'t check if it\'s already seen the listing on another website\. To avoid duplicates, we skip these listings altogether\.

*Can I use Hermes without Telegram?*
    Yes\! You can use the website with your linked account\.

*Can I thank you for building and sharing Hermes for free?*
    Yes of course, you can buy me a beer [with this Tikkie]({{}})\! {LOVE_EMOJI}

*Can I contact you?*
    Yes — see [the project on GitHub](https://github.com/xenbyte/hermes) for ways to get in touch\!""",
##
    "nl": rf"""*Waarom is Hermes gratis?*
    Ik heb Hermes voor mezelf gebouwd en toen we eenmaal een huis hadden gevonden, leek het me leuk om het met anderen te delen\!

*Welke websites checkt Hermes?*
    Gebruik het commando /websites om de volledige lijst te zien\.

*Hoe vaak checkt Hermes de websites?*
    Elke 5 minuten\.

*Kun je website \.\.\. toevoegen?*
    Waarschijnlijk wel; open een feature request op [GitHub](https://github.com/xenbyte/hermes) of kijk of het al in de discussies staat\.

*Kun je een filter toevoegen voor: aantal kamers/postcode, etc\.?*
    Kort gezegd: nee, omdat dit de dienst minder stabiel maakt\. Meer filters verhogen de complexiteit en het risico op fouten bij het scrapen, dus die vermijden we voorlopig\.

*Werkt dit ook als ik een huis wil kopen?*
    Nog niet, maar wie weet wat ik ga bouwen als ik zelf iets wil kopen\!

*Ik zag deze advertentie op Pararius maar heb geen bericht van Hermes ontvangen\. Waarom?*
    Pararius vermeldt niet voor alle woningen een huisnummer, dus Hermes kan niet controleren of de advertentie al op een andere website is gespot\. Om dubbele meldingen te voorkomen slaan we deze advertenties dus over\.

*Kan ik Hermes zonder Telegram gebruiken?*
    Ja\! Je kunt de website gebruiken met je gekoppelde account\.

*Kan ik je bedanken voor het bouwen en delen van Hermes?*
    Jazeker, je kunt [met dit Tikkie]({{}}) een biertje voor me kopen\! {LOVE_EMOJI}

*Kan ik contact met je opnemen?*
    Ja — zie [het project op GitHub](https://github.com/xenbyte/hermes) voor manieren om contact op te nemen\!"""
    },

    "link_success": {
        "en": "Your account has been linked successfully! You can now modify your filters and view results on the website.",
        "nl": "Je account is succesvol gekoppeld! Je kunt nu je filters aanpassen en woningen bekijken via de website."
    },
    "link_invalid_code": {
        "en": "Invalid or expired code. Please request a new code on the website.",
        "nl": "Ongeldige of verlopen code. Vraag een nieuwe code aan op de website."
    },
    "link_already_linked": {
        "en": "Your Telegram account is already linked to an email address.",
        "nl": "Je Telegram-account is al gekoppeld aan een e-mailadres."
    },
    "link_usage": {
        "en": "Usage: /link <code>\n\nEnter the 4-character code from the website to link your account.",
        "nl": "Gebruik: /link <code>\n\nVoer de 4-tekens code van de website in om je account te koppelen."
    },

    "pending_approval": {
        "en": "Your access request has been received. You'll be notified when you are approved.",
        "nl": "Je aanvraag is ontvangen. Je krijgt een bericht zodra je bent goedgekeurd."
    },
    "approved_notification": {
        "en": "Your access has been approved! Use /help to see what I can do for you.",
        "nl": "Je toegang is goedgekeurd! Gebruik /help om te zien wat ik voor je kan doen."
    },
    "denied_notification": {
        "en": "Your access request has been denied.",
        "nl": "Je aanvraag is helaas afgewezen."
    },

    "help": {
        "en": """*I can do the following for you:*
/start - Start receiving updates
/stop - Stop receiving updates
/faq - Show the frequently asked questions (and answers!)

/filter - Show and modify your personal filters
/websites - Show info about the websites Hermes checks
/donate - Get an open Tikkie link to show your appreciation for Hermes
/link - Link your website account to Telegram

/nl - Gebruik Hermes in het Nederlands
/en - Use Hermes in English
    """,
    "nl": """*Dit kan ik voor je doen:*
/start - Start het ontvangen van meldingen
/stop - Stop het ontvangen van meldingen
/faq - Bekijk de veelgestelde vragen (en antwoorden!)

/filter - Bekijk en wijzig je persoonlijke filters
/websites - Bekijk welke websites Hermes checkt
/donate - Ontvang een open Tikkie link en waardeer Hermes met een biertje voor de maker
/link - Koppel je website-account aan Telegram

/nl - Gebruik Hermes in het Nederlands
/en - Use Hermes in English
"""
    }
}


def get(key: str, telegram_id: int = -1, params: list[str] = []) -> str:
    user_lang = "en"
    if telegram_id != -1:
        user_lang = get_user_lang(telegram_id)
    return _STRINGS.get(key, {}).get(user_lang, f"string undefined: key={key} lang={user_lang}").format(*params)
