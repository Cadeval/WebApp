import os

import openstudio
# from openstudio import energyplus
# from energyplus import ForwardTranslator
# from openstudio.example import ExampleModel
# from openstudio.osversion import VersionTranslator
# from openstudio.openstudioutilitiescore import StringVector


def calculate_energy_metrics():
    # Create a new OpenStudio model.
    # model = openstudio.model.Model()

    # Import the IFC file
    # ifc_to_osm = openstudio.model.ModelTranslator()
    # osm_model = ifc_to_osm.loadIFCFile(openstudio.path(self.ifc_file_path))
    # Create an example model
    # model = ExampleModel().model()
    model = openstudio.model.exampleModel()

    # Modify the model (similar to what we might do with a gbXML-derived model)
    sim_control = model.getSimulationControl()
    sim_control.setRunSimulationforSizingPeriods(False)
    sim_control.setRunSimulationforWeatherFileRunPeriods(True)

    # Add output variables
    output_variables = [
        "Zone Mean Air Temperature",
        "Zone Total Internal Total Heating Energy",
        "Zone Total Internal Total Cooling Energy",
    ]

    for variable in output_variables:
        output_var = openstudio.model.OutputVariable(variable, model)
        output_var.setReportingFrequency("Hourly")

    # Save the OpenStudio model
    osm_path = openstudio.path("example_model.osm")
    model.save(osm_path, True)

    # Create an OpenStudio Workflow (OSW) for running the simulation
    osw = openstudio.WorkflowJSON()
    osw.setOswPath(osm_path)
    osw.saveAs(openstudio.path("in.osw"))

    osw.start()

    # Check if the simulation was successful
    if osw.completedStatus() == "Success":
        print("Simulation completed successfully")

        # Get the path to the SQLite results database
        sql_path = osw.sqlFile()
        if os.path.exists(sql_path):
            sql_file = openstudio.SqlFile(openstudio.path(sql_path))

            # Extract energy values
            total_site_energy = sql_file.totalSiteEnergy()
            if total_site_energy.is_initialized():
                print(f"Total Site Energy: {total_site_energy.get()} J")
            else:
                print("Could not retrieve total site energy")

            # Get annual electricity consumption
            annual_electricity = sql_file.electricityTotalEndUses()
            if annual_electricity.is_initialized():
                print(f"Annual Electricity Consumption: {annual_electricity.get()} J")
            else:
                print("Could not retrieve annual electricity consumption")

            # Get annual gas consumption
            annual_gas = sql_file.naturalGasTotalEndUses()
            if annual_gas.is_initialized():
                print(f"Annual Natural Gas Consumption: {annual_gas.get()} J")
            else:
                print("Could not retrieve annual natural gas consumption")

            # Get peak electricity demand
            peak_electricity = sql_file.electricityNetPurchased()
            if peak_electricity.is_initialized():
                print(f"Peak Electricity Demand: {max(peak_electricity.get())} W")
            else:
                print("Could not retrieve peak electricity demand")

            # Close the SQL file
            sql_file.close()
        else:
            print("Could not find SQL output file")
    else:
        print("Simulation errored out!")
