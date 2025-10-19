import click
from .assembler import parse_description, select_modules, assemble_project

@click.command()
@click.argument("description", nargs=-1)
def main(description):
    """
    Assemble a multi-language app from a description.

    Usage:
      codecraft Build a workout tracker app #mobile_friendly
    """
    desc_text = " ".join(description).strip()
    if not desc_text:
        click.echo("✖ Please supply a one-line description. Example:")
        click.echo('  codecraft "Build a workout tracker app #mobile_friendly"')
        return

    clean_desc, tags = parse_description(desc_text)
    project_name = clean_desc.replace(" ", "_").lower() or "codecraft_project"
    modules = select_modules(clean_desc, tags)
    if not modules:
        click.echo("✖ No modules selected. Try keywords like 'web', 'api', 'mobile' or tags like #mobile_friendly.")
        return
    path = assemble_project(project_name, modules, clean_desc, modules)
    click.echo(f"✔ Project '{project_name}' assembled at: {path}")
    click.echo("ℹ Run the generated setup scripts inside the project to install dependencies.")
