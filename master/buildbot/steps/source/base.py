# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members


from twisted.python import log
from buildbot.process.buildstep import LoggingBuildStep
from buildbot.status.builder import SKIPPED, FAILURE

class Source(LoggingBuildStep):
    """This is a base class to generate a source tree in the buildslave.
    Each version control system has a specialized subclass, and is expected
    to override __init__ and implement computeSourceRevision() and
    startVC(). The class as a whole builds up the self.args dictionary, then
    starts a RemoteCommand with those arguments.
    """

    renderables = [ 'workdir', 'description', 'descriptionDone' ]
    description = None # set this to a list of short strings to override
    descriptionDone = None # alternate description when the step is complete

    # if the checkout fails, there's no point in doing anything else
    haltOnFailure = True
    flunkOnFailure = True
    notReally = False

    branch = None # the default branch, should be set in __init__

    def __init__(self, workdir=None, mode='update', alwaysUseLatest=False,
                 timeout=20*60, retry=None, env=None, logEnviron=True,
                 description=None, descriptionDone=None, codebase='',
                 **kwargs):
        """
        @type  workdir: string
        @param workdir: local directory (relative to the Builder's root)
                        where the tree should be placed

        @type  mode: string
        @param mode: the kind of VC operation that is desired:
           - 'update': specifies that the checkout/update should be
             performed directly into the workdir. Each build is performed
             in the same directory, allowing for incremental builds. This
             minimizes disk space, bandwidth, and CPU time. However, it
             may encounter problems if the build process does not handle
             dependencies properly (if you must sometimes do a 'clean
             build' to make sure everything gets compiled), or if source
             files are deleted but generated files can influence test
             behavior (e.g. python's .pyc files), or when source
             directories are deleted but generated files prevent CVS from
             removing them. When used with a patched checkout, from a
             previous buildbot try for instance, it will try to "revert"
             the changes first and will do a clobber if it is unable to
             get a clean checkout. The behavior is SCM-dependent.

           - 'copy': specifies that the source-controlled workspace
             should be maintained in a separate directory (called the
             'copydir'), using checkout or update as necessary. For each
             build, a new workdir is created with a copy of the source
             tree (rm -rf workdir; cp -R -P -p copydir workdir). This
             doubles the disk space required, but keeps the bandwidth low
             (update instead of a full checkout). A full 'clean' build
             is performed each time.  This avoids any generated-file
             build problems, but is still occasionally vulnerable to
             problems such as a CVS repository being manually rearranged
             (causing CVS errors on update) which are not an issue with
             a full checkout.

           - 'clobber': specifies that the working directory should be
             deleted each time, necessitating a full checkout for each
             build. This insures a clean build off a complete checkout,
             avoiding any of the problems described above, but is
             bandwidth intensive, as the whole source tree must be
             pulled down for each build.

           - 'export': is like 'clobber', except that e.g. the 'cvs
             export' command is used to create the working directory.
             This command removes all VC metadata files (the
             CVS/.svn/{arch} directories) from the tree, which is
             sometimes useful for creating source tarballs (to avoid
             including the metadata in the tar file). Not all VC systems
             support export.

        @type  alwaysUseLatest: boolean
        @param alwaysUseLatest: whether to always update to the most
        recent available sources for this build.

        Normally the Source step asks its Build for a list of all
        Changes that are supposed to go into the build, then computes a
        'source stamp' (revision number or timestamp) that will cause
        exactly that set of changes to be present in the checked out
        tree. This is turned into, e.g., 'cvs update -D timestamp', or
        'svn update -r revnum'. If alwaysUseLatest=True, bypass this
        computation and always update to the latest available sources
        for each build.

        The source stamp helps avoid a race condition in which someone
        commits a change after the master has decided to start a build
        but before the slave finishes checking out the sources. At best
        this results in a build which contains more changes than the
        buildmaster thinks it has (possibly resulting in the wrong
        person taking the blame for any problems that result), at worst
        is can result in an incoherent set of sources (splitting a
        non-atomic commit) which may not build at all.

        @type  retry: tuple of ints (delay, repeats) (or None)
        @param retry: if provided, VC update failures are re-attempted up
                      to REPEATS times, with DELAY seconds between each
                      attempt. Some users have slaves with poor connectivity
                      to their VC repository, and they say that up to 80% of
                      their build failures are due to transient network
                      failures that could be handled by simply retrying a
                      couple times.

        @type logEnviron: boolean
        @param logEnviron: If this option is true (the default), then the
                           step's logfile will describe the environment
                           variables on the slave. In situations where the
                           environment is not relevant and is long, it may
                           be easier to set logEnviron=False.
+
        @type codebase: string
        @param codebase: Specifies which changes in a build are processed by
        the step. The default codebase value is ''. The codebase must correspond
        to a codebase assigned by the codebaseGenerator. If no codebaseGenerator
        is defined in the master then codebase doesn't need to be set, the
        default value will then match all changes.
        """

        LoggingBuildStep.__init__(self, **kwargs)
        self.addFactoryArguments(workdir=workdir,
                                 mode=mode,
                                 alwaysUseLatest=alwaysUseLatest,
                                 timeout=timeout,
                                 retry=retry,
                                 logEnviron=logEnviron,
                                 env=env,
                                 description=description,
                                 descriptionDone=descriptionDone,
                                 codebase=codebase,
                                 )

        assert mode in ("update", "copy", "clobber", "export")
        if retry:
            delay, repeats = retry
            assert isinstance(repeats, int)
            assert repeats > 0
        self.args = {'mode': mode,
                     'timeout': timeout,
                     'retry': retry,
                     'patch': None, # set during .start
                     }
        # This will get added to args later, after properties are rendered
        self.workdir = workdir

        self.sourcestamp = None
        # Codebase cannot be set yet
        self.codebase = codebase

        self.alwaysUseLatest = alwaysUseLatest

        self.logEnviron = logEnviron
        self.env = env

        descriptions_for_mode = {
            "clobber": "checkout",
            "export": "exporting"}
        descriptionDones_for_mode = {
            "clobber": "checkout",
            "export": "export"}
        if description:
            self.description = description
        else:
            self.description = [
                descriptions_for_mode.get(mode, "updating")]
            if self.codebase:
                self.description.append(self.codebase)
        if isinstance(self.description, str):
            self.description = [self.description]

        if descriptionDone:
            self.descriptionDone = descriptionDone
        else:
            self.descriptionDone = [
                descriptionDones_for_mode.get(mode, "update")]
            if self.codebase:
                self.descriptionDone.append(self.codebase)
        if isinstance(self.descriptionDone, str):
            self.descriptionDone = [self.descriptionDone]

    def setProperty(self, name, value , source):
        if self.codebase != '':
            assert not isinstance(self.getProperty(name, None), str), \
             "Sourcestep %s has a codebase, other sourcesteps don't" \
             % self.name
            property_dict = self.getProperty(name, {})
            property_dict[self.codebase] = value
            LoggingBuildStep.setProperty(self, name, property_dict, source)
        else:
            assert not isinstance(self.getProperty(name, None), dict), \
             "Sourcestep %s does not have a codebase, other sourcesteps do" \
             % self.name
            LoggingBuildStep.setProperty(self, name, value, source)

    def setStepStatus(self, step_status):
        LoggingBuildStep.setStepStatus(self, step_status)

    def setDefaultWorkdir(self, workdir):
        self.workdir = self.workdir or workdir

    def describe(self, done=False):
        if done:
            return self.descriptionDone
        return self.description

    def computeSourceRevision(self, changes):
        """Each subclass must implement this method to do something more
        precise than -rHEAD every time. For version control systems that use
        repository-wide change numbers (SVN, P4), this can simply take the
        maximum such number from all the changes involved in this build. For
        systems that do not (CVS), it needs to create a timestamp based upon
        the latest Change, the Build's treeStableTimer, and an optional
        self.checkoutDelay value."""
        return None

    def start(self):
        if self.notReally:
            log.msg("faking %s checkout/update" % self.name)
            self.step_status.setText(["fake", self.name, "successful"])
            self.addCompleteLog("log",
                                "Faked %s checkout/update 'successful'\n" \
                                % self.name)
            return SKIPPED

        # Allow workdir to be WithProperties
        self.args['workdir'] = self.workdir

        if not self.alwaysUseLatest:
            # what source stamp would this step like to use?
            s = self.build.getSourceStamp(self.codebase)
            self.sourcestamp = s

            if self.sourcestamp:
                # if branch is None, then use the Step's "default" branch
                branch = s.branch or self.branch
                # if revision is None, use the latest sources (-rHEAD)
                revision = s.revision
                if not revision:
                    revision = self.computeSourceRevision(s.changes)
                    # the revision property is currently None, so set it to something
                    # more interesting
                    if revision is not None:
                        self.setProperty('revision', str(revision), "Source")

                # if patch is None, then do not patch the tree after checkout

                # 'patch' is None or a tuple of (patchlevel, diff, root)
                # root is optional.
                patch = s.patch
                if patch:
                    self.addCompleteLog("patch", patch[1])
            else:
                log.msg("No sourcestamp found in build for codebase '%s'" % self.codebase)
                self.step_status.setText(["Codebase", '%s' % self.codebase ,"not", "in", "build" ])
                self.addCompleteLog("log",
                                    "No sourcestamp found in build for codebase '%s'" \
                                    % self.codebase)
                self.finished(FAILURE)
                return FAILURE

        else:
            revision = None
            branch = self.branch
            patch = None

        self.args['logEnviron'] = self.logEnviron
        self.args['env'] = self.env
        self.startVC(branch, revision, patch)

    def commandComplete(self, cmd):
        if cmd.updates.has_key("got_revision"):
            got_revision = cmd.updates["got_revision"][-1]
            if got_revision is not None:
                self.setProperty("got_revision", str(got_revision), "Source")
