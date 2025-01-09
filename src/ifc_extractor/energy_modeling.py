import openstudio

# Set the path to your IFC file and weather file
ifc_file_path = "path_to_your_file.ifc"
weather_file_path = "resources/schema/AUT_Vienna.Schwechat.110360_IWEC.epw"

# Create a new OpenStudio model
model = openstudio.model.Model()

# Import the IFC file
ifc_to_osm = openstudio.model.ModelTranslator()
ifc_model = ifc_to_osm.loadIFCFile(openstudio.path(ifc_file_path))

if not ifc_model:
    raise Exception("Failed to import the IFC file into the OpenStudio model.")

# Set the imported model as the active model
model = ifc_model.get()

# Set weather file
epw_file = openstudio.openstudioenergyplus.EpwFile(openstudio.path(weather_file_path))
weather_file = openstudio.model.WeatherFile.setWeatherFile(model, epw_file).get()

# Set simulation parameters
simulation_control = model.getSimulationControl()
simulation_control.setRunSimulationforSizingPeriods(True)
simulation_control.setRunSimulationforWeatherFileRunPeriods(True)

# Add necessary HVAC systems, schedules, and occupancy patterns
# This example assumes these are already defined in the IFC or added programmatically

# Save the model to an OSM file (optional)
osm_path = "path_to_save_model.osm"
model.save(openstudio.path(osm_path), True)

# Create the workflow and run the simulation
workflow = openstudio.runmanager.Workflow("modeltoidf->EnergyPlus")
job = openstudio.runmanager.JobFactory.createJob(workflow, model)
job.run()

# Extract results
sql_path = model.sqlFilePath().get()
sql_file = openstudio.SqlFile(openstudio.path(sql_path))

if sql_file.connectionOpen():
    annual_heating_demand = sql_file.annualHeatingLoad().get()
    annual_primary_energy = sql_file.annualTotalEnergy().get()
    print(f"Annual Heating Demand (HWB): {annual_heating_demand} kWh/m²a")
    print(f"Annual Primary Energy Demand (PEB): {annual_primary_energy} kWh/m²a")
else:
    raise Exception("Failed to open the SQL file for reading results.")

# Close the SQL file connection
sql_file.close()
