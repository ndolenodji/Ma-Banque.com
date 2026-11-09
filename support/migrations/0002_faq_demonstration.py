"""Articles de FAQ initiaux, cohérents avec les fonctionnalités livrées."""

from django.db import migrations

ARTICLES = [
    (
        "COMPTE",
        "Comment ouvrir un compte bancaire ?",
        "Depuis « Mes comptes », choisissez « Ouvrir un compte » puis le type de compte souhaité. "
        "L'ouverture peut être soumise à la vérification de votre identité (KYC) et à la validation "
        "de la banque. Les comptes de démonstration utilisent la devise XAF par défaut.",
        1,
    ),
    (
        "COMPTE",
        "Pourquoi mon solde disponible est-il inférieur à mon solde ?",
        "Le solde comptable inclut les montants bloqués (garanties, saisies) et les opérations en attente. "
        "Le solde disponible exclut ces montants : c'est lui qui peut être utilisé pour une nouvelle opération.",
        2,
    ),
    (
        "TRANSFERT",
        "Combien de temps prend un virement interne ?",
        "Un virement entre comptes de la plateforme est traité immédiatement et de façon atomique. "
        "Un virement programmé est exécuté à sa date d'échéance, sous réserve d'un solde suffisant.",
        1,
    ),
    (
        "TRANSFERT",
        "Puis-je annuler un virement programmé ?",
        "Oui, tant que le virement n'a pas été exécuté et qu'il est encore actif. Vous pouvez aussi le mettre "
        "en pause depuis « Virements programmés ». Une échéance déjà exécutée ne peut pas être annulée.",
        2,
    ),
    (
        "MOBILE",
        "Le paiement mobile money est-il connecté à un vrai opérateur ?",
        "Non. Les opérateurs Airtel, Moov, MTN et Orange disposent d'adaptateurs de démonstration, mais "
        "aucune API réelle n'est appelée sans contrat, identifiants et URL fournis par l'opérateur. "
        "Les écrans affichent explicitement le caractère simulé des opérations.",
        1,
    ),
    (
        "MOBILE",
        "Que se passe-t-il si un retrait mobile échoue ?",
        "Le montant et les frais réservés lors de la demande sont remboursés automatiquement et une seule fois "
        "sur le compte bancaire. Le reçu du remboursement est consultable depuis la page de suivi.",
        2,
    ),
    (
        "CARTE",
        "Comment demander une carte bancaire ?",
        "Depuis « Mes cartes », une carte virtuelle de démonstration peut être créée immédiatement après "
        "validation du KYC. Une carte physique passe par une demande soumise à validation. "
        "Aucun numéro complet de carte, CVV ou code PIN n'est stocké par l'application.",
        1,
    ),
    (
        "CARTE",
        "Que signifie le gel d'une carte ?",
        "Une carte gelée refuse les opérations simulées jusqu'à sa réactivation. Le gel ne modifie pas "
        "le solde du compte associé. Une carte annulée ne peut plus être réactivée.",
        2,
    ),
    (
        "SECURITE",
        "Comment renforcer la sécurité de mon compte ?",
        "Activez la double authentification TOTP depuis « Paramètres », conservez vos codes de secours "
        "hors ligne et vérifiez régulièrement vos sessions et appareils. Ne partagez jamais votre mot de passe, "
        "votre code PIN ou un code à usage unique.",
        1,
    ),
    (
        "SECURITE",
        "Que faire si je remarque une opération suspecte ?",
        "Gelez immédiatement votre carte si elle est concernée, changez votre mot de passe, puis ouvrez un ticket "
        "support en précisant la référence de l'opération. Le support peut consulter les traces et le journal d'audit.",
        2,
    ),
    (
        "FRAIS",
        "Quels frais s'appliquent aux opérations ?",
        "Les frais sont définis par une grille tarifaire administrable. Lorsqu'une règle est active, son montant "
        "est calculé et affiché avant la validation de l'opération concernée. Aucun frais caché n'est appliqué.",
        1,
    ),
    (
        "FRAIS",
        "Comment fonctionnent les plafonds ?",
        "Chaque compte possède des plafonds journalier, hebdomadaire et mensuel. Une opération qui les dépasserait "
        "est refusée sans mouvement de fonds. Les plafonds sont contrôlés à l'intérieur de la transaction verrouillée.",
        2,
    ),
]


def creer_articles(apps, schema_editor):
    ArticleFAQ = apps.get_model("support", "ArticleFAQ")
    for categorie, question, reponse, ordre in ARTICLES:
        ArticleFAQ.objects.update_or_create(
            question=question,
            defaults={
                "categorie": categorie,
                "reponse": reponse,
                "ordre": ordre,
                "publie": True,
            },
        )


def retirer_articles(apps, schema_editor):
    # Reverse volontairement neutre : on ne supprime jamais des contenus
    # éditoriaux qui pourraient avoir été enrichis par la banque.
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(creer_articles, retirer_articles),
    ]
