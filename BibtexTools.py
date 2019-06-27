# encoding: utf-8

import re
import collections
import urllib.request

import sublime
import sublime_plugin


def log(msg):
    if msg:
        print("[BibtexTools] {}".format(msg))


class BibtexToolsError(Exception):
    pass


BibtexEntry = collections.namedtuple("BibtexEntry", "type label region")
BibtexField = collections.namedtuple("BibtexField", "name value region")


class BibtexToolsCommand(sublime_plugin.TextCommand):

    def __init__(self, *args, **kwargs):
        super(BibtexToolsCommand, self).__init__(*args, **kwargs)
        self.settings = sublime.load_settings("BibtexTools.sublime-settings")
        self.fields = self.settings.get("fields")
        self.accents = self.settings.get("accents")
        self.accent_pattern = re.compile("|".join(list(self.accents)))

    def is_enabled(self):
        file_name = self.view.file_name()
        return file_name and file_name.split(".")[-1] == "bib"

    def get_bibtex_entries(self):

        bibtex_entries = []

        point = 0
        while point < self.view.size():

            entry_type_region = self.view.find(r"@[a-z]+", point, sublime.IGNORECASE)
            if not entry_type_region:
                break

            entry_opening = self.view.find("{", entry_type_region.end())
            entry_closing = self.get_matching_char(entry_opening.end(), "{", "}")
            entry_region = sublime.Region(
                entry_type_region.begin(),
                entry_closing.end()
            )

            entry_label_region = self.view.find(r"[^\s,]*", entry_opening.end())

            entry_type = self.view.substr(entry_type_region).lower()
            entry_label = self.view.substr(entry_label_region)

            log("Found {}{{{}}} at lines {}-{}".format(
                entry_type,
                entry_label,
                self.get_line(entry_region.begin()),
                self.get_line(entry_region.end())
            ))

            bibtex_entries.append(BibtexEntry(entry_type, entry_label, entry_region))
            point = entry_region.end()

        return bibtex_entries

    def word_left(self, point):
        """Returns the word to the left of the `point`

        Everything without whitespace is recognized as one word.

        Args:
            point (int): A point in the active view

        Returns:
            sublime.Region: Region containing the word
        """
        region = self.view.word(point)

        while not re.match(r"\s", self.view.substr(region.begin() - 1)):
            region = sublime.Region(
                self.view.word(region.begin() - 1).begin(),
                region.end()
            )

        return region

    def get_bibtex_fields(self, bibtex_entry):

        bibtex_fields = []

        point = bibtex_entry.region.begin()
        while point < bibtex_entry.region.end():

            field_definition = self.view.find(r"\s*=\s*", point)
            if not field_definition or field_definition.begin() >= bibtex_entry.region.end():
                break

            field_name_region = self.word_left(field_definition.begin() - 1)
            field_value_opening = sublime.Region(field_definition.end(), field_definition.end()+1)

            opening_closing = {"{": "}", "\"": "\""}
            field_value_opening_char = self.view.substr(field_value_opening)
            if field_value_opening_char in opening_closing.keys():
                # Field is enclosed by braces or quotes
                field_value_closing_char = opening_closing.get(field_value_opening_char)
                field_value_closing = self.get_matching_char(
                    field_value_opening.end(),
                    field_value_opening_char,
                    field_value_closing_char,
                    end=bibtex_entry.region.end()
                )

                if field_value_closing.begin() >= bibtex_entry.region.end():
                    raise BibtexToolsError(
                        "Field definition at line {} is not complete".format(
                            self.get_line(field_name_region.begin())
                        )
                    )

                field_value_region = sublime.Region(
                    field_value_opening.end(),
                    field_value_closing.begin()
                )
            else:
                # Field is not enclosed
                field_value_region = self.view.find(r"[^\s,]+", field_value_opening.begin())

            field_region = sublime.Region(
                field_name_region.begin(),
                field_value_region.end()
            )
            field_name = self.view.substr(field_name_region)
            field_value = self.view.substr(field_value_region)

            # Remove additional enclosing pairs of braces from value
            while field_value[0] == "{" and field_value[-1] == "}":
                if self.get_matching_char(
                        field_value_region.begin()+1, "{", "}",
                        end=field_value_region.end()
                ).end() == field_value_region.end():
                    field_value_region = sublime.Region(
                        field_value_region.begin() + 1,
                        field_value_region.end() - 1
                    )
                    field_value = field_value[1:-1]
                else:
                    break

            bibtex_fields.append(BibtexField(field_name, field_value, field_region))
            point = field_region.end()

        return bibtex_fields

    def get_matching_char(self, begin, opening, closing, end=None):
        if not end:
            end = self.view.size()

        point = begin
        count = 1
        while count != 0:
            if point == end:
                raise BibtexToolsError(
                    "No matching braces for entry at line {} found".format(
                        self.get_line(begin)
                    )
                )

            char = self.view.substr(point)
            if char == "\\": # Skip escape sequences
                point += 1
            elif char == opening:
                count += 1
            elif char == closing:
                count -= 1

            point += 1

        return sublime.Region(point-1, point)

    def get_line(self, point):
        return self.view.rowcol(point)[0] + 1

    def process_field(self, entry_type, field_name, field_value):
        """Formatting of fields.

        * Replaces accents with proper LaTeX code
        * Replaces dashes in ranges with en dashes (only for pages)
        * Encloses case-sensitive fields with additional braces

        Args:
            entry_type (str): Type of entry starting with @
                (@article, @book,...)
            field_name (str): Name of the field
                (author, title, ...)
            field_value (str): Value of the field without enclosing braces
                or quotes

        Returns:
            str: The processed value or an empty string if `field_type` is
                not a valid field
        """
        if not field_name in self.fields[entry_type]:
            return ""

        # Remove whitespace and replace accents
        value = self.accent_pattern.sub(
            lambda x: self.accents[x.group()],
            re.sub(r"\s+", " ", field_value)
        )

        if field_name in self.settings.get("case_sensitive"):
            value = "{{{}}}".format(value)

        if field_name == "pages":
            value = re.sub(
                r"([a-zA-Z0-9])\s*-+\s*([a-zA-Z0-9])",
                lambda x: "{}--{}".format(x.group(1), x.group(2)),
                value
            )

        return value.strip()

    def format_entry(self, entry_type, entry_label, entry_fields):

        # Remove fields with empty values
        entry_fields = collections.OrderedDict([
            (field, value) for field, value in entry_fields.items()
            if value
        ])

        entry = "{type}{{{label},".format(type=entry_type, label=entry_label)
        align = len(max(list(entry_fields), key=len))
        for field_type, field_value in entry_fields.items():
            entry += "\n{indent}{type:<{align}} = {{{value}}},".format(
                type=field_type,
                value=field_value,
                indent=self.settings.get("indentation"),
                align=align
            )
        entry += "\n}"

        return entry


class BibtexToolsFormatCommand(BibtexToolsCommand):

    def run(self, edit):

        # pylint: disable=W0201
        self.entries = {}

        self.process_view()

        sublime.status_message("Formatting...")
        self.view.erase(edit, sublime.Region(0, self.view.size()))

        for entry_type, entry_labels in iter(sorted(self.entries.items())):
            for entry_label, entry_fields in iter(sorted(entry_labels.items())):
                entry_string = self.format_entry(entry_type, entry_label, entry_fields)
                if self.view.size() > 0:
                    entry_string = "\n\n{}".format(entry_string)

                self.view.insert(edit, self.view.size(), entry_string)

    def process_view(self):

        duplicates = False

        for bibtex_entry in self.get_bibtex_entries():
            if not bibtex_entry.type in self.entries:
                self.entries[bibtex_entry.type] = {}

            if bibtex_entry.label in self.entries[bibtex_entry.type]:
                log("Duplicate entry {}".format(bibtex_entry.label))
                duplicates = True

            # Initialize fields from settings
            self.entries[bibtex_entry.type][bibtex_entry.label] = collections.OrderedDict(
                [(field, "") for field in self.fields[bibtex_entry.type]]
            )

            for bibtex_field in self.get_bibtex_fields(bibtex_entry):
                self.entries[bibtex_entry.type][bibtex_entry.label][bibtex_field.name] = self.process_field(
                    bibtex_entry.type,
                    bibtex_field.name,
                    bibtex_field.value
                )

        if duplicates:
            sublime.error_message(
                "There were duplicate entries.\nSee the console for details."
            )


class BibtexToolsSortCommand(BibtexToolsCommand):

    def run(self, edit):

        entries = {}

        for bibtex_entry in self.get_bibtex_entries():
            if not bibtex_entry.type in entries:
                entries[bibtex_entry.type] = {}

            if bibtex_entry.label in entries[bibtex_entry.type]:
                log("Duplicate entry {}".format(bibtex_entry.label))

            entries[bibtex_entry.type][bibtex_entry.label] = self.view.substr(bibtex_entry.region)

        sublime.status_message("Sorting...")
        self.view.erase(edit, sublime.Region(0, self.view.size()))

        for _, entry_labels in iter(sorted(entries.items())):
            for i, (_, entry_string) in enumerate(iter(sorted(entry_labels.items()))):
                if i > 0:
                    entry_string = "\n\n{}".format(entry_string)

                self.view.insert(edit, self.view.size(), entry_string)


class BibtexToolsFetchCommand(BibtexToolsCommand):

    url_pattern = re.compile(r"^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{2,256}\.[a-z]{2,6}\b([-a-zA-Z0-9@:%_\+.~#?&//=]*)$")

    def run(self, edit):

        doi = sublime.get_clipboard()
        url = "http://dx.doi.org/{}".format(doi)

        if self.url_pattern.match(url):
            sublime.status_message("Fetching BibTeX entry")
            log("DOI: {}".format(doi))

            request = urllib.request.Request(url)
            request.add_header("Accept", "application/x-bibtex; charset=utf-8")
            result = urllib.request.urlopen(request).read().decode("utf-8")

            lines = result.split("\n")
            entry_type, entry_label = re.match(r"(@[a-z]+){([^\s,]+)", lines[0]).groups()
            entry_label = entry_label.replace("_", "")

            # Initialize fields from settings
            entry_fields = collections.OrderedDict(
                [(field, "") for field in self.settings.get("fields")[entry_type]]
            )

            for line in lines[1:-1]:
                line = line.strip()
                pattern = r"^([a-z]+) = {(.+)},?$"
                if line.startswith("year"):
                    pattern = r"^([a-z]+) = ([0-9]+),?$"

                field_name, field_value = re.match(pattern, line).groups()
                entry_fields[field_name] = self.process_field(entry_type, field_name, field_value)

            entry = self.format_entry(entry_type, entry_label, entry_fields)

            self.view.insert(edit, self.view.sel()[0].begin(), entry)
        else:
            sublime.status_message("No valid DOI in clipboard")
