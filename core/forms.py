"""
Formulaires Django — section Gestion de RaffineryWatch.
Utilisés dans les vues de gestion (hors admin Django).
"""
from django import forms
from django.contrib.auth.models import User
from .models import Capteur, Zone, Equipement, TypeCapteur, RegleAlerte


# ── Inscription ───────────────────────────────────────────────────────────────
class InscriptionForm(forms.Form):
    username   = forms.CharField(
        max_length=150, label='Identifiant',
        widget=forms.TextInput(attrs={'placeholder': 'jean.dupont', 'autofocus': True}))
    first_name = forms.CharField(
        max_length=100, label='Prénom',
        widget=forms.TextInput(attrs={'placeholder': 'Jean'}))
    last_name  = forms.CharField(
        max_length=100, label='Nom',
        widget=forms.TextInput(attrs={'placeholder': 'Dupont'}))
    email      = forms.EmailField(
        label='Adresse email',
        widget=forms.EmailInput(attrs={'placeholder': 'jean@raffinerie.local'}))
    password1  = forms.CharField(
        label='Mot de passe', min_length=8,
        widget=forms.PasswordInput(attrs={'placeholder': 'Min. 8 caractères'}))
    password2  = forms.CharField(
        label='Confirmer le mot de passe',
        widget=forms.PasswordInput(attrs={'placeholder': 'Répétez le mot de passe'}))

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('Cet identifiant est déjà utilisé.')
        return username

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        p2 = cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError('Les deux mots de passe ne correspondent pas.')
        return cleaned


# ── Capteur ───────────────────────────────────────────────────────────────────
class CapteurForm(forms.ModelForm):
    class Meta:
        model  = Capteur
        fields = ['nom', 'type_capteur', 'zone', 'equipement', 'statut', 'seuil_min', 'seuil_max']
        widgets = {
            'nom':          forms.TextInput(attrs={'class': 'rw-input', 'placeholder': 'Ex : Capteur T-Zone A'}),
            'type_capteur': forms.Select(attrs={'class': 'rw-input'}),
            'zone':         forms.Select(attrs={'class': 'rw-input'}),
            'equipement':   forms.Select(attrs={'class': 'rw-input'}),
            'statut':       forms.Select(attrs={'class': 'rw-input'}),
            'seuil_min':    forms.NumberInput(attrs={'class': 'rw-input', 'step': '0.01'}),
            'seuil_max':    forms.NumberInput(attrs={'class': 'rw-input', 'step': '0.01'}),
        }
        labels = {
            'nom':          'Nom du capteur',
            'type_capteur': 'Type de mesure',
            'zone':         'Zone',
            'equipement':   'Équipement (optionnel)',
            'statut':       'Statut',
            'seuil_min':    'Seuil minimum',
            'seuil_max':    'Seuil maximum',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['equipement'].required = False
        self.fields['seuil_min'].required   = False
        self.fields['seuil_max'].required   = False

    def clean(self):
        cleaned = super().clean()
        smin = cleaned.get('seuil_min')
        smax = cleaned.get('seuil_max')
        if smin is not None and smax is not None and smin >= smax:
            raise forms.ValidationError('Le seuil minimum doit être inférieur au seuil maximum.')
        return cleaned


# ── Zone ──────────────────────────────────────────────────────────────────────
class ZoneForm(forms.ModelForm):
    class Meta:
        model  = Zone
        fields = ['nom', 'description']
        widgets = {
            'nom':         forms.TextInput(attrs={'class': 'rw-input', 'placeholder': 'Ex : Zone Distillation'}),
            'description': forms.Textarea(attrs={'class': 'rw-input', 'rows': 3, 'placeholder': 'Description de la zone…'}),
        }
        labels = {
            'nom':         'Nom de la zone',
            'description': 'Description',
        }


# -- Regle d'alerte -------------------------------------------------------
class RegleAlerteForm(forms.ModelForm):
    class Meta:
        model  = RegleAlerte
        fields = ['capteur', 'type_regle', 'valeur_seuil', 'priorite', 'description', 'actif']
        widgets = {
            'capteur':      forms.Select(attrs={'class': 'rw-input'}),
            'type_regle':   forms.Select(attrs={'class': 'rw-input'}),
            'valeur_seuil': forms.NumberInput(attrs={
                                'class': 'rw-input', 'step': '0.01',
                                'placeholder': 'Ex : 280.0'}),
            'priorite':     forms.Select(attrs={'class': 'rw-input'}),
            'description':  forms.TextInput(attrs={
                                'class': 'rw-input',
                                'placeholder': 'Ex : Température critique atteinte'}),
            'actif':        forms.CheckboxInput(attrs={'class': 'rw-checkbox'}),
        }
        labels = {
            'capteur':      'Capteur concerné',
            'type_regle':   'Type de règle',
            'valeur_seuil': 'Valeur seuil',
            'priorite':     'Priorité',
            'description':  'Description / message',
            'actif':        'Règle active',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['valeur_seuil'].required = False
        self.fields['description'].required  = False


# ── Utilisateur ───────────────────────────────────────────────────────────────
class UtilisateurCreateForm(forms.Form):
    username   = forms.CharField(
        max_length=150, label='Identifiant',
        widget=forms.TextInput(attrs={'class': 'rw-input', 'placeholder': 'jean.dupont'}))
    first_name = forms.CharField(
        max_length=100, label='Prénom', required=False,
        widget=forms.TextInput(attrs={'class': 'rw-input'}))
    last_name  = forms.CharField(
        max_length=100, label='Nom', required=False,
        widget=forms.TextInput(attrs={'class': 'rw-input'}))
    email      = forms.EmailField(
        label='Adresse email',
        widget=forms.EmailInput(attrs={'class': 'rw-input', 'placeholder': 'jean@raffinerie.local'}))
    password   = forms.CharField(
        label='Mot de passe', min_length=8,
        widget=forms.PasswordInput(attrs={'class': 'rw-input', 'placeholder': 'Min. 8 caractères'}))
    role       = forms.ChoiceField(
        label='Rôle',
        choices=[('operateur', 'Opérateur'), ('admin', 'Administrateur')],
        widget=forms.Select(attrs={'class': 'rw-input'}))

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('Cet identifiant est déjà utilisé.')
        return username


class UtilisateurEditForm(forms.Form):
    first_name = forms.CharField(
        max_length=100, label='Prénom', required=False,
        widget=forms.TextInput(attrs={'class': 'rw-input'}))
    last_name  = forms.CharField(
        max_length=100, label='Nom', required=False,
        widget=forms.TextInput(attrs={'class': 'rw-input'}))
    email      = forms.EmailField(
        label='Adresse email',
        widget=forms.EmailInput(attrs={'class': 'rw-input'}))
    role       = forms.ChoiceField(
        label='Rôle',
        choices=[('operateur', 'Opérateur'), ('admin', 'Administrateur')],
        widget=forms.Select(attrs={'class': 'rw-input'}))
    new_password = forms.CharField(
        label='Nouveau mot de passe (laisser vide pour ne pas changer)',
        required=False, min_length=8,
        widget=forms.PasswordInput(attrs={'class': 'rw-input',
                                          'placeholder': 'Laisser vide si inchange'}))
