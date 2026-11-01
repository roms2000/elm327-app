"""Édition des profils véhicule."""

from __future__ import annotations

from PySide6.QtWidgets import (
	QComboBox,
	QDialog,
	QDialogButtonBox,
	QDoubleSpinBox,
	QFormLayout,
	QHBoxLayout,
	QInputDialog,
	QLabel,
	QLineEdit,
	QMessageBox,
	QPushButton,
	QSpinBox,
	QVBoxLayout,
	QWidget,
)

from obdtool.core.profile import (
	FUELS,
	VehicleProfile,
	delete_profile,
	list_profiles,
	save_profile,
	tyre_circumference_m,
)


def _spin(value: float, low: float, high: float, decimals: int = 2, suffix: str = "") -> QDoubleSpinBox:
	spin = QDoubleSpinBox()
	spin.setRange(low, high)
	spin.setDecimals(decimals)
	spin.setValue(value)
	if suffix:
		spin.setSuffix(suffix)
	return spin


class ProfileDialog(QDialog):
	"""Choix et édition du profil véhicule utilisé par les calculs."""

	def __init__(self, current: VehicleProfile, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.setWindowTitle("Profil véhicule")
		self.profiles = list_profiles()
		self.selected = next((p for p in self.profiles if p.name == current.name), self.profiles[0])
		layout = QVBoxLayout(self)
		row = QHBoxLayout()
		self.selector = QComboBox()
		self.selector.currentIndexChanged.connect(self._select)
		row.addWidget(self.selector, 1)
		for label, slot in (("Nouveau…", self._new), ("Dupliquer…", self._duplicate), ("Supprimer", self._delete)):
			button = QPushButton(label)
			button.clicked.connect(slot)
			row.addWidget(button)
		layout.addLayout(row)

		form = QFormLayout()
		self.make = QLineEdit()
		self.model = QLineEdit()
		self.engine = QLineEdit()
		self.year = QSpinBox()
		self.year.setRange(1990, 2100)
		self.fuel = QComboBox()
		for key, (label, afr, density) in FUELS.items():
			self.fuel.addItem(f"{label} (AFR {afr:g}, {density:g} kg/L)", key)
		self.displacement = _spin(1598, 50, 10000, 0, " cm³")
		self.cylinders = QSpinBox()
		self.cylinders.setRange(1, 16)
		self.ve = _spin(0.85, 0.3, 1.3, 3)
		self.ve.setToolTip(
			"Rendement volumétrique moyen, utilisé pour estimer le débit d'air sans débitmètre (speed-density).\n"
			"0,80–0,90 pour un moteur atmosphérique ; à ajuster en comparant la consommation calculée à un plein."
		)
		self.mass = _spin(1150, 300, 5000, 0, " kg")
		self.ratios = QLineEdit()
		self.ratios.setPlaceholderText("3.727 ; 2.048 ; 1.393 ; 1.029 ; 0.820")
		self.final = _spin(4.214, 1.0, 10.0, 3)
		self.tyre = QLineEdit()
		self.tyre.textChanged.connect(self._tyre_changed)
		self.tyre_info = QLabel()
		self.cd = _spin(0.33, 0.15, 1.0, 3)
		self.area = _spin(2.1, 1.0, 6.0, 2, " m²")
		self.crr = _spin(0.012, 0.005, 0.03, 4)
		self.notes = QLineEdit()
		for label, widget in (
			("Marque :", self.make),
			("Modèle :", self.model),
			("Moteur :", self.engine),
			("Année :", self.year),
			("Carburant :", self.fuel),
			("Cylindrée :", self.displacement),
			("Cylindres :", self.cylinders),
			("Rendement volumétrique :", self.ve),
			("Masse (avec conducteur) :", self.mass),
			("Rapports de boîte :", self.ratios),
			("Rapport de pont :", self.final),
			("Pneus :", self.tyre),
			("", self.tyre_info),
			("Coefficient de traînée (Cx) :", self.cd),
			("Surface frontale :", self.area),
			("Résistance au roulement :", self.crr),
			("Notes :", self.notes),
		):
			form.addRow(label, widget)
		layout.addLayout(form)
		warning = QLabel(
			"Les valeurs calculées (débit d'air, consommation, puissance) sont des estimations qui dépendent de la "
			"justesse de ce profil."
		)
		warning.setWordWrap(True)
		warning.setObjectName("pageSubtitle")
		layout.addWidget(warning)
		buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
		buttons.accepted.connect(self._save)
		buttons.rejected.connect(self.reject)
		layout.addWidget(buttons)
		self._fill_selector(self.selected.name)

	def _fill_selector(self, name: str) -> None:
		self.selector.blockSignals(True)
		self.selector.clear()
		for profile in self.profiles:
			self.selector.addItem(profile.name)
		self.selector.setCurrentIndex(max(self.selector.findText(name), 0))
		self.selector.blockSignals(False)
		self._select()

	def _select(self) -> None:
		self.selected = self.profiles[max(self.selector.currentIndex(), 0)]
		p = self.selected
		self.make.setText(p.make)
		self.model.setText(p.model)
		self.engine.setText(p.engine)
		self.year.setValue(p.year)
		self.fuel.setCurrentIndex(max(self.fuel.findData(p.fuel), 0))
		self.displacement.setValue(p.displacement_cc)
		self.cylinders.setValue(p.cylinders)
		self.ve.setValue(p.volumetric_efficiency)
		self.mass.setValue(p.mass_kg)
		self.ratios.setText(" ; ".join(f"{r:g}" for r in p.gear_ratios))
		self.final.setValue(p.final_drive)
		self.tyre.setText(p.tyre)
		self.cd.setValue(p.drag_coefficient)
		self.area.setValue(p.frontal_area_m2)
		self.crr.setValue(p.rolling_resistance)
		self.notes.setText(p.notes)

	def _tyre_changed(self, text: str) -> None:
		self.tyre_info.setText(f"Circonférence de roulement estimée : {tyre_circumference_m(text):.3f} m")

	def _collect(self, profile: VehicleProfile) -> VehicleProfile:
		try:
			ratios = [float(x.replace(",", ".")) for x in self.ratios.text().replace(";", " ").split() if x.strip()]
		except ValueError:
			ratios = profile.gear_ratios
		profile.make = self.make.text().strip().lower() or "generique"
		profile.model = self.model.text().strip()
		profile.engine = self.engine.text().strip()
		profile.year = self.year.value()
		profile.fuel = str(self.fuel.currentData())
		profile.displacement_cc = self.displacement.value()
		profile.cylinders = self.cylinders.value()
		profile.volumetric_efficiency = self.ve.value()
		profile.mass_kg = self.mass.value()
		profile.gear_ratios = [r for r in ratios if r > 0] or profile.gear_ratios
		profile.final_drive = self.final.value()
		profile.tyre = self.tyre.text().strip()
		profile.drag_coefficient = self.cd.value()
		profile.frontal_area_m2 = self.area.value()
		profile.rolling_resistance = self.crr.value()
		profile.notes = self.notes.text().strip()
		return profile

	def _ask_name(self, title: str, default: str) -> str | None:
		name, ok = QInputDialog.getText(self, title, "Nom du profil :", text=default)
		name = name.strip()
		if not ok or not name:
			return None
		if any(p.name == name for p in self.profiles):
			QMessageBox.warning(self, title, "Ce nom existe déjà.")
			return None
		return name

	def _new(self) -> None:
		name = self._ask_name("Nouveau profil", "Mon véhicule")
		if name:
			profile = VehicleProfile(name=name, make="generique", model="", engine="", notes="")
			save_profile(profile)
			self.profiles.append(profile)
			self._fill_selector(name)

	def _duplicate(self) -> None:
		name = self._ask_name("Dupliquer le profil", f"{self.selected.name} (copie)")
		if name:
			copy = VehicleProfile.from_dict(self._collect(VehicleProfile.from_dict(self.selected.to_dict())).to_dict())
			copy.name = name
			save_profile(copy)
			self.profiles.append(copy)
			self._fill_selector(name)

	def _delete(self) -> None:
		if len(self.profiles) <= 1:
			QMessageBox.information(self, "Profil", "Il faut conserver au moins un profil.")
			return
		if QMessageBox.question(self, "Profil", f"Supprimer le profil « {self.selected.name} » ?") == (
			QMessageBox.StandardButton.Yes
		):
			delete_profile(self.selected)
			self.profiles.remove(self.selected)
			self._fill_selector(self.profiles[0].name)

	def _save(self) -> None:
		save_profile(self._collect(self.selected))
		self.accept()
